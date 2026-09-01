"""With "both" asked for, the clean side is downloaded ONCE, as the fullest cut.

WHAT THIS FENCES OFF
--------------------
A clean cut and its explicit twin land in one edition group on purpose, and the
explicit preference picks the side that gets planned. With BOTH asked for the
losing side is still wanted, so it is downloaded whole instead of being thrown
away.

"Whole" used to mean "every edition of it". The kept side got the merge and, on
a decline, the completeness collapse that leaves one fullest version. The losing
side got neither. So someone who asked for both sides and whose artist has a
standard and a deluxe pressing of the same record ended up with the clean
standard AND the clean deluxe queued together: every song of the standard
written to disk a second time, in a second folder, for nothing.

The losing side now goes through the same completeness collapse. What is pinned
here:

* "both" queues ONE clean edition, the fullest, next to whatever the explicit
  side produces, and the subset edition is gone by identity, not just by count;
* "explicit" and "clean" are untouched by this: the losing side still leaves
  with the group, and the kept side still behaves exactly as before;
* "keep both when unsure" survives on the losing side. Two clean editions that
  are not a subset of one another (an extra song, or a same-titled song at a
  different length) are BOTH still queued. Collapsing those would lose music,
  which is far worse than a duplicate;
* the losing side is weighed against ITSELF and never against the side that
  won. A clean cut lists the same song titles at the same lengths as its
  explicit twin, so weighing the two sides together reads the clean copy as
  redundant and drops it: the person who asked for both kinds gets only the
  explicit one;
* an edition whose songs could not be read is never collapsed away;
* the kept side's own collapse still runs when its merge declines, so the fix
  added a collapse rather than moving one;
* the queue order matches the order the editions arrived in.
"""

from __future__ import annotations

from unittest.mock import patch

from waves.waves_ui.backend import WavesBridge, _MergeRec


class _Song:
    """The little a merge plan reads off a track object."""

    def __init__(self, sid):
        self.id = sid

    def __repr__(self):  # pragma: no cover - assertion output only
        return f"<song {self.id}>"


class _Edition:
    """One album edition as the merge path sees it."""

    def __init__(self, name, aid):
        self.name = name
        self.id = aid

    def __repr__(self):  # pragma: no cover - assertion output only
        return f"<{self.name}>"


def _songs(prefix, explicit, *titles_and_lengths):
    """Track records for one edition. No ISRCs, so cross-edition matching falls
    back to title plus length, which is the ordinary case."""
    return [
        _MergeRec(_Song(f"{prefix}{i + 1}"), title, dur, None, explicit)
        for i, (title, dur) in enumerate(titles_and_lengths)
    ]


def _queue(albums, recs, mode, ranks=None):
    """Run the real WavesBridge._merge_editions over one edition group.

    The bridge is a carcass with only the three things the method reads hung on
    it: the explicit preference, the track-record source, and the audio-rank
    function. Everything the method then calls (the explicit split, the merge
    planner, the collapse) is the real production code.

    ``ranks`` maps an album id or a track id to an audio-quality rank, default 1.
    """
    ranks = ranks or {}
    bridge = WavesBridge.__new__(WavesBridge)
    bridge._waves_prefs = {"explicit_mode": mode}
    bridge._merge_recs_factory = lambda: (lambda album: recs[id(album)])
    bridge._merge_rank_fn = lambda: (lambda obj: ranks.get(str(getattr(obj, "id", "")), 1))
    # One release, so the split and the collapse are what is under test rather
    # than the edition keying.
    with patch("waves.waves_ui.backend._edition_base_key", lambda album: "one release"):
        return bridge._merge_editions(list(albums))


def _twin_pressings():
    """An explicit standard and deluxe, and the clean twin of each.

    Every song of the standard is on the deluxe as well, same title and same
    length, which is what makes the standard a redundant download once the
    deluxe is queued."""
    exp_std = _Edition("Album [Explicit]", "exp-std")
    exp_dlx = _Edition("Album (Deluxe) [Explicit]", "exp-dlx")
    cln_std = _Edition("Album [Clean]", "cln-std")
    cln_dlx = _Edition("Album (Deluxe) [Clean]", "cln-dlx")
    recs = {
        id(exp_std): _songs("es", True, ("one", 200), ("two", 240)),
        id(exp_dlx): _songs("ed", True, ("one", 200), ("two", 240), ("bonus", 180)),
        id(cln_std): _songs("cs", False, ("one", 200), ("two", 240)),
        id(cln_dlx): _songs("cd", False, ("one", 200), ("two", 240), ("bonus", 180)),
    }
    return exp_std, exp_dlx, cln_std, cln_dlx, recs


def test_both_queues_only_the_fullest_clean_edition():
    """Asking for both sides must not write the same songs to disk twice.

    The clean standard's songs are all on the clean deluxe. Queue both and the
    person gets two folders holding the same music, and pays for the download
    twice. Only the deluxe should be queued."""
    exp_std, exp_dlx, cln_std, cln_dlx, recs = _twin_pressings()
    plain, plans = _queue([exp_std, exp_dlx, cln_std, cln_dlx], recs, "both", ranks={"es1": 4})

    assert plain == [cln_dlx], f"the clean side should be the deluxe alone, got {plain!r}"
    assert cln_std not in plain, "the clean standard was queued beside its own deluxe, every song twice"
    assert [identity for identity, _plan in plans] == [exp_dlx], "the explicit side stopped merging"


def test_a_lone_clean_twin_is_still_queued():
    """One clean cut and no clean deluxe: the clean copy is still downloaded.

    This is the ordinary shape of a release, one clean twin, and it is the case
    where the collapse has nothing to do. The clean cut lists the same songs at
    the same lengths as the explicit ones, so on titles and lengths alone it
    reads as already contained in the explicit deluxe. It is not the same
    music. Weigh it against the explicit side and the clean copy is thrown out
    as a duplicate: the person asked for both kinds and would get only the
    explicit deluxe, with the clean version they wanted never downloaded."""
    exp_std = _Edition("Album [Explicit]", "exp-std")
    exp_dlx = _Edition("Album (Deluxe) [Explicit]", "exp-dlx")
    cln_std = _Edition("Album [Clean]", "cln-std")
    recs = {
        id(exp_std): _songs("es", True, ("one", 200), ("two", 240)),
        id(exp_dlx): _songs("ed", True, ("one", 200), ("two", 240), ("bonus", 180)),
        id(cln_std): _songs("cs", False, ("one", 200), ("two", 240)),
    }
    plain, plans = _queue([exp_std, exp_dlx, cln_std], recs, "both", ranks={"es1": 4})

    assert plain == [cln_std], f"the album's only clean version was not queued, got {plain!r}"
    assert [identity for identity, _plan in plans] == [exp_dlx], "the explicit side stopped merging"


def test_the_losing_side_is_weighed_against_itself_only():
    """A clean edition is never dropped for being covered by an EXPLICIT one.

    The clean standard's three songs are all on the explicit deluxe, at the
    same lengths, but they are the explicit recordings: handing the person that
    deluxe instead is handing them the version they asked to also have a clean
    copy of. The clean tour edition carries a song neither of the others has,
    so nothing on the clean side is redundant and both clean editions are
    queued."""
    exp_dlx = _Edition("Album (Deluxe) [Explicit]", "exp-dlx")
    cln_std = _Edition("Album [Clean]", "cln-std")
    cln_alt = _Edition("Album (Tour Edition) [Clean]", "cln-alt")
    recs = {
        id(exp_dlx): _songs("ed", True, ("one", 200), ("two", 240), ("three", 260), ("bonus", 180)),
        id(cln_std): _songs("cs", False, ("one", 200), ("two", 240), ("three", 260)),
        id(cln_alt): _songs("ca", False, ("one", 200), ("four", 300)),
    }
    plain, plans = _queue([exp_dlx, cln_std, cln_alt], recs, "both")

    assert plans == []
    assert cln_std in plain, "the clean standard was dropped because the EXPLICIT deluxe covers it"
    assert plain == [cln_std, cln_alt, exp_dlx], f"expected both clean editions then the explicit one, got {plain!r}"


def test_an_explicit_preference_is_untouched():
    """With only the explicit side wanted, the clean twins still leave with the
    group and the explicit merge is unchanged."""
    exp_std, exp_dlx, cln_std, cln_dlx, recs = _twin_pressings()
    plain, plans = _queue([exp_std, exp_dlx, cln_std, cln_dlx], recs, "explicit", ranks={"es1": 4})

    assert plain == [], "a version the user asked not to have was queued anyway"
    assert [identity for identity, _plan in plans] == [exp_dlx]


def test_a_clean_preference_is_untouched():
    """With only the clean side wanted, the explicit twins leave, and the clean
    side still collapses to its fullest edition through the kept-side path."""
    exp_std, exp_dlx, cln_std, cln_dlx, recs = _twin_pressings()
    plain, plans = _queue([exp_std, exp_dlx, cln_std, cln_dlx], recs, "clean", ranks={"es1": 4})

    assert plans == [], "a clean preference was handed a merge built from explicit editions"
    assert plain == [cln_dlx], f"the clean side should be the deluxe alone, got {plain!r}"
    assert exp_std not in plain and exp_dlx not in plain


def test_both_sides_collapse_when_the_merge_declines():
    """Nothing to upgrade, so no merge happens on either side. Both sides should
    still come back as one fullest edition each, the clean side first because
    that is the order the editions arrived in."""
    exp_std, exp_dlx, cln_std, cln_dlx, recs = _twin_pressings()
    plain, plans = _queue([exp_std, exp_dlx, cln_std, cln_dlx], recs, "both")

    assert plans == [], "there was no quality upgrade to merge for"
    assert plain == [cln_dlx, exp_dlx], f"expected one clean and one explicit edition, got {plain!r}"


def test_the_fullest_clean_edition_wins_even_when_it_is_the_lower_tier():
    """The clean deluxe carries the extra songs but is offered at a lower audio
    tier than the clean standard. The person asked for one clean version, the
    fullest one, so the deluxe is what gets queued: keeping the standard instead
    would silently drop songs, and keeping both writes the shared songs twice."""
    exp = _Edition("Album [Explicit]", "exp")
    cln_std = _Edition("Album [Clean]", "cln-std")
    cln_dlx = _Edition("Album (Deluxe) [Clean]", "cln-dlx")
    recs = {
        id(exp): _songs("e", True, ("one", 200), ("two", 240)),
        id(cln_std): _songs("cs", False, ("one", 200), ("two", 240)),
        id(cln_dlx): _songs("cd", False, ("one", 200), ("two", 240), ("bonus", 180)),
    }
    plain, plans = _queue([exp, cln_std, cln_dlx], recs, "both", ranks={"cln-std": 4, "cln-dlx": 1})

    assert plans == []
    assert plain == [cln_dlx, exp], f"expected the fullest clean edition plus the explicit one, got {plain!r}"
    assert cln_std not in plain, "the shorter clean edition was queued too, its songs land on disk twice"


def test_two_clean_cuts_that_really_differ_are_both_kept():
    """A clean standard whose "two" is a different, longer recording than the
    deluxe's is not contained in the deluxe at all. Collapsing it away would lose
    that recording, so both editions stay queued. Keep both when unsure."""
    exp = _Edition("Album [Explicit]", "exp")
    cln_dlx = _Edition("Album (Deluxe) [Clean]", "cln-dlx")
    cln_std = _Edition("Album [Clean]", "cln-std")
    recs = {
        id(exp): _songs("e", True, ("one", 200), ("two", 240)),
        id(cln_dlx): _songs("cd", False, ("one", 200), ("two", 240), ("bonus", 180)),
        id(cln_std): _songs("cs", False, ("one", 200), ("two", 305)),
    }
    plain, plans = _queue([exp, cln_dlx, cln_std], recs, "both")

    assert plans == []
    assert cln_std in plain, "a clean edition holding a longer cut of a song was collapsed away"
    assert cln_dlx in plain
    assert plain == [cln_dlx, cln_std, exp], f"expected both clean editions then the explicit one, got {plain!r}"


def test_the_losing_side_never_collapses_to_nothing():
    """Two clean listings of the same songs must not cancel each other out.

    Neither is the fuller one, so neither may be dropped for being contained in
    the other. Drop them for that and the person's clean copy of the album
    disappears from the download entirely, which is far worse than the duplicate
    the collapse is there to prevent."""
    exp = _Edition("Album [Explicit]", "exp")
    cln_a = _Edition("Album [Clean]", "cln-a")
    cln_b = _Edition("Album (Reissue) [Clean]", "cln-b")
    recs = {
        id(exp): _songs("e", True, ("one", 200), ("two", 240)),
        id(cln_a): _songs("ca", False, ("one", 200), ("two", 240)),
        id(cln_b): _songs("cb", False, ("one", 200), ("two", 240)),
    }
    plain, plans = _queue([exp, cln_a, cln_b], recs, "both")

    assert plans == []
    assert cln_a in plain and cln_b in plain, "the clean side collapsed to nothing, the album is not downloaded"
    assert plain == [cln_a, cln_b, exp], f"expected both clean listings then the explicit one, got {plain!r}"


def test_an_extra_song_on_the_losing_side_keeps_its_edition_and_the_order():
    """Three clean editions: a deluxe, an alternate that carries a song the
    deluxe does not, and a standard fully contained in the deluxe. Only the
    standard is redundant. The two survivors come back in the order they were
    listed in."""
    exp = _Edition("Album [Explicit]", "exp")
    cln_dlx = _Edition("Album (Deluxe) [Clean]", "cln-dlx")
    cln_alt = _Edition("Album (Tour Edition) [Clean]", "cln-alt")
    cln_std = _Edition("Album [Clean]", "cln-std")
    recs = {
        id(exp): _songs("e", True, ("one", 200), ("two", 240)),
        id(cln_dlx): _songs("cd", False, ("one", 200), ("two", 240), ("bonus", 180)),
        id(cln_alt): _songs("ca", False, ("one", 200), ("three", 500)),
        id(cln_std): _songs("cs", False, ("one", 200), ("two", 240)),
    }
    plain, plans = _queue([exp, cln_dlx, cln_alt, cln_std], recs, "both")

    assert plans == []
    assert cln_alt in plain, "an edition with an exclusive song was collapsed away"
    assert cln_std not in plain, "the redundant clean standard was queued as well"
    assert plain == [cln_dlx, cln_alt, exp], f"the queue order does not follow the listing order: {plain!r}"


def test_an_edition_whose_songs_could_not_be_read_is_still_queued():
    """A pressing whose track list the app could not fetch is unknown, not empty.
    It must never be collapsed away on a guess, or the person quietly loses an
    album they asked for.

    An unreadable edition always stays on the kept side, because the clean or
    explicit side an edition belongs to is decided from those same song lists,
    so it can never be the one that loses. The collapse that keeps it is the
    kept side's."""
    exp_std, exp_dlx, cln_std, cln_dlx, recs = _twin_pressings()
    mystery = _Edition("Album (Remastered) [Explicit]", "mystery")
    recs[id(mystery)] = []
    plain, plans = _queue([exp_std, exp_dlx, cln_std, cln_dlx, mystery], recs, "both")

    assert plans == [], "an edition of unknown content must not be merged over"
    assert mystery in plain, "an edition whose songs could not be read was dropped"
    assert plain == [cln_dlx, exp_dlx, mystery], f"expected one clean, one explicit, plus the unknown: {plain!r}"
