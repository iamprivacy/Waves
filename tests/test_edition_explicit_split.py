"""A clean cut and its explicit twin never merge into each other.

WHAT THIS FENCES OFF
--------------------
The edition key strips the [Explicit] / [Clean] marker on purpose, so a clean
cut and its explicit twin always land in one edition group. Neither may borrow
from the other: a clean preference must never be handed an explicit recording,
and an explicit preference keeps its own cut of a song rather than trading it
for a clean one that happens to be the better file.

That much the matcher already refused. What it could not do was tell the twin
apart from a genuinely different tracklist: the unmatched tracks made the
group fail the superset guard, so the whole group declined with "not_superset"
and TWO EDITIONS THAT ALIGNED PERFECTLY LOST THEIR MERGE because a third,
the clean twin, was standing in the group.

The group is now split before planning. What is pinned here:

* the split happens on the songs the editions really disagree about, proved by
  the merge matcher itself, never on a release-wide flag. The earlier attempt
  partitioned on that flag and broke: tidalapi defaults an album's explicit to
  True and a track's to False, and an "explicit" release with no profanity on
  it is byte-identical to its clean twin. An untouched group must plan exactly
  as it did before;
* two explicit editions still merge with a clean twin in the group;
* the losing side is never a source: no borrowed track ever crosses;
* the discography path picks the side from the preference, and with BOTH asked
  for the losing side is still downloaded whole rather than dropped;
* the single-album path picks the side of the album that was CLICKED, whatever
  the preference says, and says so when it declines.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tidaler.waves_ui.backend import (
    WavesBridge,
    _align_edition,
    _build_merge_plan,
    _explicit_sides,
    _MergeRec,
    _split_explicit_editions,
)


def _rec(obj, title, dur, isrc=None, explicit=False):
    return _MergeRec(obj, title, dur, isrc, explicit)


class _Album:
    """Only what the planner reads off an edition."""

    def __init__(self, name, aid):
        self.name = name
        self.id = aid


def _t(tid, rank=1):
    return SimpleNamespace(id=tid, track_num=None, volume_num=None, rank=rank)


def _plan(group, recs, ranks):
    return _build_merge_plan(group, lambda a: recs[id(a)], lambda o: ranks.get(getattr(o, "id", ""), 1))


# ---- the matcher's new question --------------------------------------------------
def test_cross_explicit_matching_is_off_by_default():
    """Planning must never see a clean cut as the same recording."""
    a = [_rec(None, "song", 200, "X", True)]
    b = [_rec(None, "song", 200, "X", False)]
    assert _align_edition(a, b) == {}


def test_cross_explicit_matching_finds_the_twin_when_asked():
    a = [_rec(None, "song", 200, "X", True)]
    b = [_rec(None, "song", 200, "X", False)]
    assert _align_edition(a, b, cross_explicit=True) == {0: b[0]}


def test_cross_explicit_matching_finds_a_twin_that_carries_its_own_code():
    """The ordinary catalog shape, and the only one the split really meets.

    A clean edit IS a different recording, so it normally carries its own
    ISRC instead of reusing the explicit cut's. While the differing-code veto
    applied here too, the matcher answered "not the same song" to every such
    twin: no side was ever taken, no edition was ever dropped, and the twin
    stayed in the group to veto the merge its siblings could do. The whole
    feature only ever fired on releases that stamp one code on both cuts,
    which breaks ISO 3901 and is the rare case, not the rule."""
    a = [_rec(None, "song", 200, "US1", True)]
    b = [_rec(None, "song", 200, "US9", False)]
    assert _align_edition(a, b, cross_explicit=True) == {0: b[0]}


def test_planning_still_refuses_two_recordings_with_different_codes():
    """The veto is lifted for the split's question ONLY. Planning borrows
    audio, so there a differing ISRC must stay positive proof of a different
    recording and beat a matching title and length."""
    a = [_rec(None, "song", 200, "US1", True)]
    b = [_rec(None, "song", 200, "US9", True)]
    assert _align_edition(a, b) == {}


# ---- sides -----------------------------------------------------------------------
def test_editions_that_agree_take_no_side():
    """The whole safety of this: a group nobody disputes is untouched."""
    x, y = _Album("std", "1"), _Album("deluxe", "2")
    recs = {
        id(x): [_rec(_t("a"), "one", 200, "I1"), _rec(_t("b"), "two", 200, "I2")],
        id(y): [_rec(_t("c"), "one", 200, "I1"), _rec(_t("d"), "two", 200, "I2")],
    }
    assert _explicit_sides([x, y], recs) == {}
    assert _split_explicit_editions([x, y], recs, True) == ([x, y], [])


def test_an_all_clean_explicit_release_is_never_split_from_its_twin():
    """The failure mode that reverted the first attempt: an "explicit" release
    with no profanity on it carries the same recordings as the clean edition,
    and the release-wide flag says otherwise. Nothing here reads that flag, so
    these two still merge."""
    x, y = _Album("std [Explicit]", "1"), _Album("std [Clean]", "2")
    x.explicit, y.explicit = True, False  # the release flag, deliberately ignored
    recs = {
        id(x): [_rec(_t("a"), "one", 200, "I1"), _rec(_t("b"), "two", 200, "I2")],
        id(y): [_rec(_t("c"), "one", 200, "I1")],
    }
    assert _explicit_sides([x, y], recs) == {}
    kept, dropped = _split_explicit_editions([x, y], recs, True)
    assert (kept, dropped) == ([x, y], [])


def test_the_twin_takes_the_other_side():
    exp, cln = _Album("std [Explicit]", "1"), _Album("std [Clean]", "2")
    recs = {
        id(exp): [_rec(_t("a"), "one", 200, "I1", True)],
        id(cln): [_rec(_t("b"), "one", 200, "I1", False)],
    }
    sides = _explicit_sides([exp, cln], recs)
    assert sides == {id(exp): True, id(cln): False}
    assert _split_explicit_editions([exp, cln], recs, True) == ([exp], [cln])
    assert _split_explicit_editions([exp, cln], recs, False) == ([cln], [exp])


def test_the_twin_takes_the_other_side_when_it_carries_its_own_code():
    """Same as above with the codes a real catalog hands out. Every other
    sides test here stamps one ISRC on both cuts, so the suite could stay
    green while the split never fired on an actual release."""
    exp, cln = _Album("std [Explicit]", "1"), _Album("std", "2")
    recs = {
        id(exp): [_rec(_t("a"), "one", 200, "US1", True)],
        id(cln): [_rec(_t("b"), "one", 200, "US9", False)],
    }
    sides = _explicit_sides([exp, cln], recs)
    assert sides == {id(exp): True, id(cln): False}
    assert _split_explicit_editions([exp, cln], recs, True) == ([exp], [cln])
    assert _split_explicit_editions([exp, cln], recs, False) == ([cln], [exp])


def test_an_edition_holding_both_sides_counts_as_explicit():
    """``mixed`` carries the explicit cut of one disputed song and the clean
    cut of another. It counts as the explicit side, because that is the side
    that can hand a clean preference what it asked not to have."""
    mixed = _Album("mixed", "1")
    cln = _Album("clean", "2")  # disputes song one, holding it clean
    exp = _Album("explicit", "3")  # disputes song two, holding it explicit
    recs = {
        id(mixed): [_rec(_t("a"), "one", 200, "I1", True), _rec(_t("b"), "two", 200, "I2", False)],
        id(cln): [_rec(_t("c"), "one", 200, "I1", False)],
        id(exp): [_rec(_t("d"), "two", 200, "I2", True)],
    }
    group = [mixed, cln, exp]
    assert _explicit_sides(group, recs) == {id(mixed): True, id(cln): False, id(exp): True}
    assert _split_explicit_editions(group, recs, False) == ([cln], [mixed, exp])
    assert _split_explicit_editions(group, recs, True) == ([mixed, exp], [cln])


def test_a_pair_that_disputes_both_ways_leaves_a_clean_preference_nothing():
    """Both editions hold an explicit cut of something, so neither is safe for
    a clean preference and the merge is simply off. Conservative on purpose:
    the fallback downloads the clicked album whole."""
    a, b = _Album("a", "1"), _Album("b", "2")
    recs = {
        id(a): [_rec(_t("a1"), "one", 200, "I1", True), _rec(_t("a2"), "two", 200, "I2", False)],
        id(b): [_rec(_t("b1"), "one", 200, "I1", False), _rec(_t("b2"), "two", 200, "I2", True)],
    }
    kept, dropped = _split_explicit_editions([a, b], recs, False)
    assert kept == [] and dropped == [a, b]


# ---- the merge the twin used to block --------------------------------------------
def test_two_explicit_editions_still_merge_with_a_clean_twin_in_the_group():
    """The bug this fixes. All three key alike; the clean twin aligned with
    neither explicit edition, the superset guard fired, and the merge those two
    could clearly do was lost."""
    std = _Album("std [Explicit]", "1")
    deluxe = _Album("deluxe [Explicit]", "2")
    clean = _Album("std [Clean]", "3")
    recs = {
        id(deluxe): [
            _rec(_t("d1"), "one", 200, "I1", True),
            _rec(_t("d2"), "two", 200, "I2", True),
            _rec(_t("d3"), "bonus", 200, "I3", True),
        ],
        id(std): [_rec(_t("s1", 4), "one", 200, "I1", True), _rec(_t("s2"), "two", 200, "I2", True)],
        id(clean): [_rec(_t("c1"), "one", 200, "I1", False), _rec(_t("c2"), "two", 200, "I2", False)],
    }
    ranks = {"s1": 4}
    group = [std, deluxe, clean]

    # Before the split: the clean twin kills it.
    assert _plan(group, recs, ranks)[2] == "not_superset"

    kept, dropped = _split_explicit_editions(group, recs, True)
    assert dropped == [clean]
    identity, plan, reason = _plan(kept, recs, ranks)
    assert reason == "" and identity is deluxe
    assert [e.src.id for e in plan] == ["s1", "d2", "d3"], "the merge did not take the better shared track"


def test_the_merge_survives_a_clean_twin_that_carries_its_own_codes():
    """The same rescue as above on a release whose clean twin stamps its own
    ISRCs, which is what a catalog normally does. This is the shape a user
    actually clicks: the twin took no side, nothing was dropped, the superset
    guard fired, and the two explicit editions lost the merge they could
    plainly do while the status line said there was nothing to borrow."""
    std = _Album("std [Explicit]", "1")
    deluxe = _Album("deluxe [Explicit]", "2")
    clean = _Album("std", "3")
    recs = {
        id(deluxe): [
            _rec(_t("d1"), "one", 200, "US1", True),
            _rec(_t("d2"), "two", 200, "US2", True),
            _rec(_t("d3"), "bonus", 200, "US3", True),
        ],
        id(std): [_rec(_t("s1", 4), "one", 200, "US1", True), _rec(_t("s2"), "two", 200, "US2", True)],
        id(clean): [_rec(_t("c1"), "one", 200, "US8", False), _rec(_t("c2"), "two", 200, "US9", False)],
    }
    ranks = {"s1": 4}
    group = [std, deluxe, clean]

    assert _plan(group, recs, ranks)[2] == "not_superset", "the clean twin still kills it unsplit"

    kept, dropped = _split_explicit_editions(group, recs, True)
    assert dropped == [clean], "the twin was not recognised, so the merge below cannot happen"
    identity, plan, reason = _plan(kept, recs, ranks)
    assert reason == "" and identity is deluxe
    assert [e.src.id for e in plan] == ["s1", "d2", "d3"], "the merge did not take the better shared track"


def test_a_borrowed_track_never_crosses_the_divide():
    """Even with the twin kept in the group by hand, planning may not source a
    slot from the other side: the matcher refuses the pairing outright, so a
    higher-quality clean cut can never replace an explicit one."""
    exp, cln = _Album("std [Explicit]", "1"), _Album("std [Clean]", "2")
    recs = {
        id(exp): [_rec(_t("e1"), "one", 200, "I1", True), _rec(_t("e2"), "two", 200, "I2", True)],
        id(cln): [_rec(_t("c1", 4), "one", 200, "I1", False), _rec(_t("c2", 4), "two", 200, "I2", False)],
    }
    identity, plan, reason = _plan([exp, cln], recs, {"c1": 4, "c2": 4})
    assert plan is None and reason == "not_superset", "a clean recording was borrowed into an explicit album"


def test_a_group_that_is_only_a_twin_pair_has_no_merge_left():
    exp, cln = _Album("std [Explicit]", "1"), _Album("std [Clean]", "2")
    recs = {
        id(exp): [_rec(_t("e1"), "one", 200, "I1", True)],
        id(cln): [_rec(_t("c1"), "one", 200, "I1", False)],
    }
    kept, dropped = _split_explicit_editions([exp, cln], recs, True)
    assert kept == [exp] and dropped == [cln]
    assert len(kept) < 2, "nothing to merge, and the caller must fall back to a plain download"


# ---- an edition that takes no side stays, whichever side wins --------------------
def _neutral_group():
    """Deluxe and std carry the explicit cut of "one", the clean twin carries
    the clean cut, and a remaster carries only "two", a song with no profanity
    that every edition holds the same way. The remaster disputes nothing, so
    it never appears in the sides map, and it holds the best copy of "two".

    This is the one shape where the split's default is actually read: every
    other sides test either has an empty sides map (the early return) or a
    map that names EVERY edition, so a wrong default there changes nothing."""
    deluxe = _Album("deluxe [Explicit]", "1")
    std = _Album("std [Explicit]", "2")
    clean = _Album("std [Clean]", "3")
    remaster = _Album("std (Remastered)", "4")
    recs = {
        id(deluxe): [
            _rec(_t("d1"), "one", 200, "I1", True),
            _rec(_t("d2"), "two", 200, "I2", False),
            _rec(_t("d3"), "bonus", 200, "I3", True),
        ],
        id(std): [_rec(_t("s1"), "one", 200, "I1", True), _rec(_t("s2"), "two", 200, "I2", False)],
        id(clean): [_rec(_t("c1"), "one", 200, "I1", False), _rec(_t("c2"), "two", 200, "I2", False)],
        id(remaster): [_rec(_t("r2", 4), "two", 200, "I2", False)],
    }
    return deluxe, std, clean, remaster, recs, {"r2": 4}


def test_an_edition_that_takes_no_side_is_kept_by_either_preference():
    """The remaster is absent from the sides map, so only the split's default
    decides its fate, and that default must read as "stays" for BOTH values
    of the preference. Dropping it would throw away the best copy of a song
    nobody disputes; keeping it can hand nobody the wrong cut, because it
    aligns with everyone."""
    deluxe, std, clean, remaster, recs, _ranks = _neutral_group()
    group = [deluxe, std, clean, remaster]
    sides = _explicit_sides(group, recs)
    assert sides == {id(deluxe): True, id(std): True, id(clean): False}
    assert id(remaster) not in sides, "the remaster disputes nothing and must take no side"

    assert _split_explicit_editions(group, recs, True) == ([deluxe, std, remaster], [clean])
    assert _split_explicit_editions(group, recs, False) == ([clean, remaster], [deluxe, std])


def test_an_edition_that_takes_no_side_still_lends_its_better_copy():
    """End to end: the neutral remaster survives the split and the plan over
    the kept side borrows "two" from it. Had the split dropped it, the two
    explicit editions would have had nothing to trade and the merge would have
    declined with "no_upgrade" while a better file sat right there."""
    deluxe, std, clean, remaster, recs, ranks = _neutral_group()
    kept, dropped = _split_explicit_editions([deluxe, std, clean, remaster], recs, True)
    assert dropped == [clean]
    identity, plan, reason = _plan(kept, recs, ranks)
    assert reason == "" and identity is deluxe, reason
    assert [e.src.id for e in plan] == ["d1", "r2", "d3"], "the better copy on the neutral edition was not borrowed"

    # The counterfactual, so the assertion above cannot be read as luck: with the
    # neutral edition gone the two explicit editions have nothing better to
    # trade, and the user is told there is nothing to merge.
    assert _plan([deluxe, std], recs, ranks)[2] == "no_upgrade"


def test_a_neutral_edition_listed_first_gets_the_same_answer():
    """The sides map is built pairwise from the group order and the default is
    read per edition, so the neutral one must land the same way wherever it
    stands in the group. The two entry points feed different orders."""
    deluxe, std, clean, remaster, recs, ranks = _neutral_group()
    group = [remaster, std, deluxe, clean]
    sides = _explicit_sides(group, recs)
    assert id(remaster) not in sides and set(sides) == {id(deluxe), id(std), id(clean)}

    assert _split_explicit_editions(group, recs, True) == ([remaster, std, deluxe], [clean])
    assert _split_explicit_editions(group, recs, False) == ([remaster, clean], [std, deluxe])

    kept, _dropped = _split_explicit_editions(group, recs, True)
    identity, plan, reason = _plan(kept, recs, ranks)
    assert reason == "" and identity is deluxe
    assert [e.src.id for e in plan] == ["d1", "r2", "d3"]


# ---- the two entry points --------------------------------------------------------
class _InlinePool:
    def start(self, worker):
        worker.run() if hasattr(worker, "run") else worker()


class _Signal:
    def __init__(self):
        self.emits = []

    def emit(self, *a):
        self.emits.append(a if len(a) > 1 else a[0])


def _twin_group():
    """An explicit standard, an explicit deluxe it can upgrade from, and the
    clean twin of the standard. Two of these three merge; the third may not."""
    std = _Album("std [Explicit]", "s")
    deluxe = _Album("deluxe [Explicit]", "d")
    clean = _Album("std [Clean]", "c")
    recs = {
        id(deluxe): [
            _rec(_t("d1"), "one", 200, "I1", True),
            _rec(_t("d2"), "two", 200, "I2", True),
            _rec(_t("d3"), "bonus", 200, "I3", True),
        ],
        id(std): [_rec(_t("s1"), "one", 200, "I1", True), _rec(_t("s2"), "two", 200, "I2", True)],
        id(clean): [_rec(_t("c1"), "one", 200, "I1", False), _rec(_t("c2"), "two", 200, "I2", False)],
    }
    return std, deluxe, clean, recs


class _DiscoStub:
    """_merge_editions with the group already decided, so the split is what is
    under test rather than the edition keying."""

    _merge_editions = WavesBridge._merge_editions

    def __init__(self, mode, recs):
        self._waves_prefs = {"explicit_mode": mode}
        self._recs = recs
        self.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=SimpleNamespace(name="HI_RES_LOSSLESS")))

    def _merge_recs_factory(self):
        return lambda a: self._recs[id(a)]

    def _merge_rank_fn(self):
        return lambda o: 4 if getattr(o, "id", "") == "s1" else 1


def _disco(mode):
    std, deluxe, clean, recs = _twin_group()
    stub = _DiscoStub(mode, recs)
    with patch("tidaler.waves_ui.backend._edition_base_key", lambda a: "one-group"):
        plain, plans = stub._merge_editions([std, deluxe, clean])
    return std, deluxe, clean, plain, plans


def test_the_discography_merges_the_preferred_side():
    std, deluxe, clean, plain, plans = _disco("explicit")
    assert len(plans) == 1, "the two explicit editions did not merge"
    assert plans[0][0] is deluxe
    assert clean not in plain, "an explicit preference still downloaded the clean twin"


def test_the_discography_keeps_the_clean_side_when_both_are_asked_for():
    std, deluxe, clean, plain, plans = _disco("both")
    assert len(plans) == 1 and plans[0][0] is deluxe
    assert clean in plain, "BOTH was asked for and the clean edition was dropped entirely"


def test_a_clean_preference_takes_the_clean_side_and_never_merges_into_it():
    std, deluxe, clean, plain, plans = _disco("clean")
    assert plans == [], "a clean preference was handed a merge built from explicit editions"
    assert clean in plain, "the clean edition the preference asked for was not downloaded"
    assert std not in plain and deluxe not in plain


class _ClickStub:
    downloadAlbumBestOfBoth = WavesBridge.downloadAlbumBestOfBoth

    def __init__(self, clicked, group, recs, mode="explicit"):
        self._objs = {"album": {"x": clicked}}
        self._dl = object()
        self._merge_plans = {}
        self._merge_scanned: set = set()
        self._scan_pool = _InlinePool()
        self._waves_prefs = {"explicit_mode": mode}
        self.downloadState = _Signal()
        self._albumsQueued = _Signal()
        self.statuses: list = []
        self._group = group
        self._recs = recs

    def _set_status(self, text):
        self.statuses.append(text)

    def _sibling_editions(self, obj):
        return list(self._group), True

    def _merge_recs_factory(self):
        return lambda a: self._recs[id(a)]

    def _merge_rank_fn(self):
        return lambda o: 4 if getattr(o, "id", "") == "s1" else 1

    def _remember(self, bucket, key, obj):
        self._objs.setdefault(bucket, {})[key] = obj


def test_clicking_the_clean_twin_never_returns_the_explicit_merge():
    """The click asked for THIS album. Answering it with the explicit version
    is the one thing a merge must never do, whatever the preference says."""
    std, deluxe, clean, recs = _twin_group()
    stub = _ClickStub(clean, [std, deluxe, clean], recs, mode="explicit")
    stub.downloadAlbumBestOfBoth("x")
    assert stub._merge_plans == {}, "clicking the clean edition queued an explicit merge"
    assert stub._albumsQueued.emits == ["x"] or stub._albumsQueued.emits == [["x"]]
    assert any("clean or explicit twin" in s for s in stub.statuses), stub.statuses


def test_clicking_an_explicit_edition_merges_its_own_side():
    std, deluxe, clean, recs = _twin_group()
    stub = _ClickStub(std, [std, deluxe, clean], recs, mode="explicit")
    stub.downloadAlbumBestOfBoth("x")
    assert list(stub._merge_plans) == ["d"], stub._merge_plans
    plan = stub._merge_plans["d"]
    assert [e.src.id for e in plan] == ["s1", "d2", "d3"]
    assert all("twin" not in s for s in stub.statuses), stub.statuses
