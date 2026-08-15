"""Unit tests for the album-presence matching core (tidaler.matching).

Pure-function tests: no network and no Qt runtime, imported straight from the
headless brain rather than through the GUI bridge. These are the regression
contract for the cross-catalog "do I already have this album?" check against
the scanned local library. The whole design is biased against FALSE POSITIVES,
so several cases assert a match is deliberately HIDDEN and would be a conscious
change to loosen.

Two bars are under test and they are not the same. ``present`` lights the pill
and is generous: being wrong there costs a badge. ``partial`` False is the claim
that a local copy is COMPLETE, which the UI renders as an inert already
downloaded button, so being wrong THERE costs the user a download they cannot
start. Every case below asserting ``not partial`` is asserting that stronger
bar, and loosening one is a decision about file access, not about a pill.

``partial`` is itself the AND of two independent axes with their own section
below: ``sure`` (identity, the badge's "?") and ``full`` (coverage, N OF M).
"""

import pytest

from tidaler.matching import (
    canon as _canon,
)
from tidaler.matching import (
    decide_presence as _decide_presence,
)
from tidaler.matching import (
    disc_group as _disc_group,
)
from tidaler.matching import (
    edition_key as _edition_key,
)
from tidaler.matching import (
    presence_key as _presence_key,
)
from tidaler.matching import (
    same_edition as _same_edition,
)
from tidaler.matching import (
    strip_edition_quals_ext as _strip_edition_quals_ext,
)


def _index(*entries):
    """Build a presence index (as _rebuild_library_index will) from scanned
    (title, artist, year, tracks, folder_path) tuples.

    The row keeps its raw ``title`` alongside the key it is filed under, because
    the key has its edition qualifiers peeled off and the completeness gate
    compares the unpeeled titles."""
    idx = {}
    for title, artist, year, tracks, fp in entries:
        idx.setdefault(_presence_key(title, artist), []).append(
            {"title": title, "year": year, "tracks": tracks, "id": fp}
        )
    return idx


# ---- _canon: cross-catalog punctuation / unicode folding --------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Rockin’ Years", "Rockin' Years"),  # curly apostrophe -> straight
        ("‘Heroes’", "'Heroes'"),  # curly single quotes
        ("“Heroes”", '"Heroes"'),  # curly double quotes
        ("Wait…", "Wait..."),  # ellipsis -> three dots
        ("1967–1970", "1967-1970"),  # en dash -> hyphen
        ("Live—At Home", "Live-At Home"),  # em dash -> hyphen
    ],
)
def test_canon_folds_punctuation(raw, expected):
    assert _canon(raw) == expected


def test_canon_nfkc_composed_vs_decomposed():
    # Built from escapes, not literals: a file save can silently normalise two
    # visually identical literals to one codepoint sequence.
    composed = "Beyonc\u00e9"  # e-acute as one codepoint
    decomposed = "Beyonce\u0301"  # e + combining acute
    assert composed != decomposed
    assert _canon(composed) == _canon(decomposed)


def test_canon_none_safe():
    assert _canon(None) == ""


# ---- _strip_edition_quals_ext: parenthetical AND dash-form editions ---------
@pytest.mark.parametrize(
    "title,expected",
    [
        # delegates to the shared helper for parenthetical/bracket forms
        ("album (deluxe edition)", "album"),
        ("album [deluxe]", "album"),
        ("album (expanded edition)", "album"),
        # presence-local addition: dash-form editions some taggers use
        ("album - deluxe edition", "album"),
        ("album - expanded edition", "album"),
        ("album - deluxe", "album"),
        ("album - bonus track version", "album"),
        # NOT stripped: year ranges and bare-year dash tails (no edition word)
        ("1967 - 1970", "1967 - 1970"),
        ("live - 1970", "live - 1970"),
        # NOT stripped: KEEP markers stay a distinct release (remaster/live/...)
        ("album (2019 remaster)", "album (2019 remaster)"),
        ("album - remastered", "album - remastered"),
        # only the edition tail is peeled, an earlier dash is preserved
        ("foo - bar - deluxe edition", "foo - bar"),
    ],
)
def test_strip_edition_quals_ext(title, expected):
    assert _strip_edition_quals_ext(title) == expected


# ---- _presence_key: both components, canonicalised --------------------------
def test_presence_key_folds_apostrophe_and_edition():
    # curly-apostrophe TIDAL title + dash-form local edition collapse to one key
    assert _presence_key("Rockin’ Years (Deluxe Edition)", "Dolly Parton") == _presence_key(
        "Rockin' Years - Deluxe Edition", "dolly parton"
    )


def test_presence_key_requires_both_title_and_artist():
    assert _presence_key("Same Title", "Artist A") != _presence_key("Same Title", "Artist B")


# ---- decide_presence --------------------------------------------------------
def test_present_full_match():
    idx = _index(("Discovery", "Daft Punk", "2001", 14, "/lib/dp/discovery"))
    r = _decide_presence("Discovery", "Daft Punk", "2001", 14, idx)
    assert r["present"] and not r["partial"]
    assert r["local_album_id"] == "/lib/dp/discovery" and r["local_tracks"] == 14


def test_absent_album_hidden():
    idx = _index(("Discovery", "Daft Punk", "2001", 14, "/lib/dp/discovery"))
    assert _decide_presence("Homework", "Daft Punk", "1997", 16, idx)["present"] is False


def test_empty_index_hidden():
    assert _decide_presence("Discovery", "Daft Punk", "2001", 14, {})["present"] is False
    assert _decide_presence("Discovery", "Daft Punk", "2001", 14, None)["present"] is False


def test_same_title_same_artist_different_year_rejected():
    # The Weezer colour LPs: identical (title, artist), separated only by year.
    idx = _index(
        ("Weezer", "Weezer", "1994", 10, "blue"),
        ("Weezer", "Weezer", "2001", 10, "green"),
        ("Weezer", "Weezer", "2008", 10, "red"),
    )
    assert _decide_presence("Weezer", "Weezer", "2008", 10, idx)["local_album_id"] == "red"
    assert _decide_presence("Weezer", "Weezer", "1994", 10, idx)["local_album_id"] == "blue"
    # a colour we don't own (2016) matches none of the three
    assert _decide_presence("Weezer", "Weezer", "2016", 10, idx)["present"] is False


def test_year_tolerance_absorbs_reissue_drift():
    idx = _index(("Album", "Artist", "2019", 12, "rk"))
    assert _decide_presence("Album", "Artist", "2020", 12, idx)["present"] is True  # within 1
    assert _decide_presence("Album", "Artist", "2022", 12, idx)["present"] is False  # too far


def test_missing_year_never_rejects():
    idx = _index(("Album", "Artist", "", 12, "rk"))  # local album with no year tag
    assert _decide_presence("Album", "Artist", "2019", 12, idx)["present"] is True
    idx2 = _index(("Album", "Artist", "2019", 12, "rk"))
    assert _decide_presence("Album", "Artist", "", 12, idx2)["present"] is True


def test_single_under_album_name_reads_partial():
    # A lead single (1 track) filed under the LP name must never claim the album.
    idx = _index(("Album", "Artist", "2020", 1, "single"))
    r = _decide_presence("Album", "Artist", "2020", 13, idx)
    assert r["present"] and r["partial"] and r["local_tracks"] == 1


def test_standard_vs_deluxe_reads_partial():
    idx = _index(("Album", "Artist", "2019", 11, "std"))
    r = _decide_presence("Album", "Artist", "2019", 15, idx)  # TIDAL deluxe
    assert r["present"] and r["partial"]


def test_own_deluxe_open_standard_shows_the_pill_but_does_not_gate():
    # Holding "Album (Deluxe)" and opening the plain "Album" reports presence
    # (the pill is right: you do have those tracks) but deliberately does NOT
    # claim completeness, because the only way to allow it is to gate on the
    # edition-stripped key, and that same looseness is what let "Untitled
    # (Black Is)" satisfy "Untitled (Rise)". The cost of this choice is one
    # redundant live Download button on an album you already own; the cost of
    # the alternative is a download you cannot start. See test below.
    idx = _index(("Album (Deluxe)", "Artist", "2019", 15, "deluxe"))
    r = _decide_presence("Album", "Artist", "2019", 13, idx)  # TIDAL standard
    assert r["present"] and r["partial"]


def test_different_albums_sharing_a_stripped_key_never_gate_each_other():
    # The Sault case: two distinct records whose titles collapse to the same
    # presence key once the trailing parenthetical is peeled.
    idx = _index(("Untitled (Rise)", "Sault", "2020", 20, "rise"))
    r = _decide_presence("Untitled (Black Is)", "Sault", "2020", 20, idx)
    assert r["partial"] is True


def test_off_by_one_track_reads_partial():
    # A copy short by exactly one track used to satisfy the gate (the bar was
    # `>= tt - 1`), so an album missing its closer rendered as fully downloaded.
    idx = _index(("Album", "Artist", "2019", 12, "rk"))
    r = _decide_presence("Album", "Artist", "2019", 13, idx)
    assert r["present"] and r["partial"] is True


def test_picks_best_candidate_by_track_count():
    # two editions share a key+year; the fuller one is chosen
    idx = _index(
        ("Album", "Artist", "2019", 10, "std"),
        ("Album", "Artist", "2019", 15, "deluxe"),
    )
    r = _decide_presence("Album", "Artist", "2019", 15, idx)
    assert r["local_album_id"] == "deluxe" and not r["partial"]


def test_zero_track_local_album_hidden():
    idx = _index(("Album", "Artist", "2019", 0, "empty"))
    assert _decide_presence("Album", "Artist", "2019", 12, idx)["present"] is False


def test_unknown_tidal_track_count_is_present_but_never_complete():
    # tidalapi reports num_tracks as None/-1 when numberOfTracks is absent from
    # the payload. There is then nothing to compare the local count against, so
    # completeness is unprovable and must not be claimed.
    idx = _index(("Album", "Artist", "2019", 12, "rk"))
    r = _decide_presence("Album", "Artist", "2019", 0, idx)
    assert r["present"] and r["partial"] is True


def test_undated_local_folder_is_present_but_never_complete():
    # An untagged folder survives the year filter against EVERY same-titled
    # album, so all four Weezer colour LPs matched one folder. It may light the
    # pill; it may never gate.
    idx = _index(("Weezer", "Weezer", "", 10, "colour"))
    for tidal_year in ("1994", "2001", "2008", "2016"):
        r = _decide_presence("Weezer", "Weezer", tidal_year, 10, idx)
        assert r["present"] and r["partial"] is True


# ---- the two independent axes: sure (identity) and full (coverage) ----------
# ``partial`` conflated them once, and a 12-of-12 undated folder rendered as
# "partially in library" (nothing partial about the copy, the match was merely
# unproven). ``sure`` answers "is this really the same album" (the badge's "?"),
# ``full`` answers "does the copy hold every track" (N OF M); ``partial`` stays
# the strict both-axes bar for the claim button and the bulk skip gate.
def test_full_match_is_sure_and_full():
    idx = _index(("Discovery", "Daft Punk", "2001", 14, "/lib/dp/discovery"))
    r = _decide_presence("Discovery", "Daft Punk", "2001", 14, idx)
    assert r["sure"] is True and r["full"] is True and r["partial"] is False


def test_undated_but_complete_copy_is_full_not_sure():
    idx = _index(("Album", "Artist", "", 10, "undated"))
    r = _decide_presence("Album", "Artist", "2020", 10, idx)
    assert r["present"] and r["full"] is True and r["sure"] is False
    assert r["partial"] is True  # the strict bar holds: an unproven match never gates


def test_short_but_proven_copy_is_sure_not_full():
    idx = _index(("Album", "Artist", "2019", 9, "rk"))
    r = _decide_presence("Album", "Artist", "2019", 12, idx)
    assert r["present"] and r["sure"] is True and r["full"] is False
    assert r["partial"] is True


def test_edition_qualifier_mismatch_is_never_sure():
    # Deluxe on disk, standard opened: the key matches but identity is unproven
    # (the same looseness that let "Untitled (Black Is)" satisfy "Untitled (Rise)").
    idx = _index(("Album (Deluxe)", "Artist", "2019", 15, "deluxe"))
    r = _decide_presence("Album", "Artist", "2019", 13, idx)
    assert r["present"] and r["sure"] is False


def test_unknown_tidal_track_count_is_never_full():
    # No source count means coverage is unprovable, however proven the identity.
    idx = _index(("Album", "Artist", "2019", 12, "rk"))
    r = _decide_presence("Album", "Artist", "2019", 0, idx)
    assert r["present"] and r["sure"] is True and r["full"] is False


def test_hidden_verdict_carries_both_axes_false():
    r = _decide_presence("Album", "Artist", "2019", 12, {})
    assert r["present"] is False and r["sure"] is False and r["full"] is False


def test_album_with_no_artist_never_matches():
    # An empty artist reduces presence_key to a title-only key, which matches
    # any local folder sharing a generic title.
    idx = _index(("Greatest Hits", "", "1998", 14, "someones"))
    assert _decide_presence("Greatest Hits", "", "1998", 14, idx)["present"] is False


@pytest.mark.parametrize("credit", ["VA", "V.A.", "V/A", "va", "Various", "Varios"])
def test_abbreviated_various_artists_credits_never_match(credit):
    # Compilations credited in short form collide across unrelated comps exactly
    # as the spelled-out ones do.
    idx = _index(("Summer Hits", credit, "2019", 18, "comp"))
    assert _decide_presence("Summer Hits", credit, "2019", 18, idx)["present"] is False


def test_a_real_artist_is_not_mistaken_for_a_various_artists_credit():
    # The short-form pattern is anchored to the whole name, so it must not fire
    # on real names that merely contain those letters.
    idx = _index(("Album", "Vanessa", "2019", 12, "rk"))
    assert _decide_presence("Album", "Vanessa", "2019", 12, idx)["present"] is True
    idx2 = _index(("Album", "Various Cruelties", "2019", 12, "rk"))
    assert _decide_presence("Album", "Various Cruelties", "2019", 12, idx2)["present"] is True


def test_various_artists_never_matches():
    idx = _index(("Now That's What I Call Music", "Various Artists", "2020", 20, "va"))
    assert _decide_presence("Now That's What I Call Music", "Various Artists", "2020", 20, idx)["present"] is False


def test_diacritic_and_edition_still_match():
    # decomposed diacritic on the local side, dash-edition on the local side,
    # composed + parenthetical on the TIDAL side -> one key.
    idx = _index(("Vespertine - Deluxe", "Björk", "2001", 14, "rk"))
    r = _decide_presence("Vespertine (Deluxe Edition)", "Björk", "2001", 14, idx)
    assert r["present"] and r["local_album_id"] == "rk"


def test_kept_remaster_does_not_match_plain_album():
    # A local remaster must NOT be claimed as ownership of the plain TIDAL album
    # (the upgrade must stay visible).
    idx = _index(("Rumours (2013 Remaster)", "Fleetwood Mac", "2013", 11, "rk"))
    assert _decide_presence("Rumours", "Fleetwood Mac", "1977", 11, idx)["present"] is False


# ---- Multi-disc releases ----------------------------------------------------
# The scanner calls any directory that directly holds audio an album, so a
# two-disc release indexes as two albums with half the tracks each and the pill
# reads "9 OF 18" for a record the user owns in full. Adding the halves up is
# the fix, and adding up the WRONG things is how a live Download button becomes
# an inert one, so nothing is summed without an explicit disc marker in the
# folder name. The refusals below matter more than the matches.


@pytest.mark.parametrize(
    "folder,expected",
    [
        ("/m/A/Album/CD1", ("/m/A/Album", "")),
        ("/m/A/Album/CD 2", ("/m/A/Album", "")),
        ("/m/A/Album/Disc 2", ("/m/A/Album", "")),
        ("/m/A/Album/disk_3", ("/m/A/Album", "")),
        (r"C:\m\A\Album\CD2", ("C:/m/A/Album", "")),  # Windows separators
        ("/m/A/Album (Disc 1)", ("/m/A", "album")),
        ("/m/A/Album [CD2]", ("/m/A", "album")),
        # No disc marker: nothing to group.
        ("/m/A/Album", None),
        ("/m/A/Album (Deluxe)", None),
        # A duplicate-download folder. Waves makes these itself on a name
        # collision, and two PARTIAL attempts at one album summed would read as
        # a complete copy: the one case this must never fire on.
        ("/m/A/Album_1", None),
        ("/m/A/Album (1)", None),
        # Words that merely start like a disc marker, and a bare number.
        ("/m/A/Discovery", None),
        ("/m/A/Disclosure", None),
        ("/m/A/CD", None),
        ("/m/A/Disc", None),
    ],
)
def test_disc_group_only_fires_on_an_explicit_disc_marker(folder, expected):
    assert _disc_group(folder) == expected


def test_discs_inside_the_album_folder_are_one_album():
    idx = _index(
        ("Big Album", "A", "2019", 9, "/m/A/Big Album/CD1"),
        ("Big Album", "A", "2019", 9, "/m/A/Big Album/CD2"),
    )
    r = _decide_presence("Big Album", "A", "2019", 18, idx)
    assert r["local_tracks"] == 18
    assert r["partial"] is False
    # Reveal opens the album, not disc 2 of it.
    assert r["local_album_id"] == "/m/A/Big Album"


def test_sibling_disc_folders_are_one_album():
    idx = _index(
        ("Big Album", "A", "2019", 9, "/m/A/Big Album (Disc 1)"),
        ("Big Album", "A", "2019", 9, "/m/A/Big Album (Disc 2)"),
    )
    r = _decide_presence("Big Album", "A", "2019", 18, idx)
    assert r["local_tracks"] == 18 and r["partial"] is False
    # No single folder holds the set, so the chosen disc stays the reveal target.
    assert r["local_album_id"].startswith("/m/A/Big Album (Disc ")


def test_a_genuinely_incomplete_disc_set_stays_partial():
    # Disc 2 is missing: 9 of 18 is still 9 of 18, and the button stays live.
    idx = _index(("Big Album", "A", "2019", 9, "/m/A/Big Album/CD1"))
    r = _decide_presence("Big Album", "A", "2019", 18, idx)
    assert r["present"] and r["partial"] is True and r["local_tracks"] == 9


def test_two_partial_download_attempts_are_never_summed():
    # THE case the disc-marker requirement exists for. Waves creates "_NN"
    # folders when a name collides, so two interrupted attempts at one album sit
    # side by side under the same parent with identical tags. Summing them would
    # claim a complete copy the user does not have and leave them unable to
    # download it.
    idx = _index(
        ("Album", "A", "2019", 5, "/m/A/Album"),
        ("Album", "A", "2019", 7, "/m/A/Album_1"),
    )
    r = _decide_presence("Album", "A", "2019", 12, idx)
    assert r["local_tracks"] == 7, "separate copies must not be added together"
    assert r["partial"] is True


def test_a_remaster_beside_the_original_is_never_summed():
    idx = _index(
        ("Album", "A", "2019", 6, "/m/A/Album"),
        ("Album (2020 Remaster)", "A", "2019", 6, "/m/A/Album (2020 Remaster)"),
    )
    r = _decide_presence("Album", "A", "2019", 12, idx)
    assert r["local_tracks"] == 6 and r["partial"] is True


def test_discs_of_different_editions_are_not_summed_together():
    # A deluxe disc set and a standard disc set under one parent: the disc
    # marker is present on all four, but the titles differ with their
    # qualifiers intact, so the two sets stay apart.
    idx = _index(
        ("Album", "A", "2019", 5, "/m/A/Album (Disc 1)"),
        ("Album", "A", "2019", 5, "/m/A/Album (Disc 2)"),
        ("Album (Deluxe)", "A", "2019", 5, "/m/A/Album (Deluxe) (Disc 1)"),
        ("Album (Deluxe)", "A", "2019", 5, "/m/A/Album (Deluxe) (Disc 2)"),
    )
    r = _decide_presence("Album", "A", "2019", 10, idx)
    assert r["local_tracks"] == 10, "the standard set only"
    assert r["partial"] is False


def test_a_single_disc_album_is_untouched():
    idx = _index(("Album", "A", "2019", 12, "/m/A/Album"))
    r = _decide_presence("Album", "A", "2019", 12, idx)
    assert r["local_tracks"] == 12 and r["partial"] is False and r["local_album_id"] == "/m/A/Album"


def test_two_rips_of_the_same_disc_are_never_summed():
    # Two rips of DISC 1, in two naming styles, share the group and the tags.
    # They are one disc, not a set: summed they claimed a complete album the
    # user owns only half of, and the button went inert.
    sibling = _index(
        ("Album", "A", "2019", 9, "/m/A/Album (Disc 1)"),
        ("Album", "A", "2019", 9, "/m/A/Album [CD 1]"),
    )
    r = _decide_presence("Album", "A", "2019", 18, sibling)
    assert r["local_tracks"] == 9 and r["partial"] is True
    inside = _index(
        ("Album", "A", "2019", 9, "/m/A/Album/CD1"),
        ("Album", "A", "2019", 9, "/m/A/Album/Disc 1"),
    )
    r = _decide_presence("Album", "A", "2019", 18, inside)
    assert r["local_tracks"] == 9 and r["partial"] is True


@pytest.mark.parametrize("folder", ["/m/X/ABCD2", "/m/X/Paradis 2", "/m/X/Amadis 12"])
def test_a_title_merely_ending_in_a_marker_like_stem_is_not_a_disc(folder):
    # No separator before the "cd", or a word that merely contains "dis": a
    # title, not a disc marker. Reading these as discs let two numbered sibling
    # volumes with one shared series tag sum into a false complete copy.
    assert _disc_group(folder) is None


def test_numbered_sibling_volumes_are_never_summed():
    # "Paradis 1" and "Paradis 2": two different 8-track volumes, both tagged
    # with the bare series title. Not discs of anything.
    idx = _index(
        ("Paradis", "X", "2019", 8, "/m/X/Paradis 1"),
        ("Paradis", "X", "2019", 8, "/m/X/Paradis 2"),
    )
    r = _decide_presence("Paradis", "X", "2019", 16, idx)
    assert r["local_tracks"] == 8 and r["partial"] is True


def test_an_undated_disc_never_completes_a_set():
    # The survivor filter only rejects a year conflict when BOTH sides carry
    # one, so an undated disc 2 (possibly a different pressing entirely)
    # survived it and completed the set. It must not: refusal costs a partial
    # pill and a live button, the fail-safe direction.
    idx = _index(
        ("Album", "A", "2005", 10, "/m/A/Album (Disc 1)"),
        ("Album", "A", "", 10, "/m/A/Album (Disc 2)"),
    )
    r = _decide_presence("Album", "A", "2005", 20, idx)
    assert r["local_tracks"] == 10 and r["partial"] is True


# --- The release's own declared shape -----------------------------------------
# Counting audio files says what the user HOLDS. Only the files' own tracktotal
# and discnumber say what the release CONTAINS, and the gap between those two
# is every case below: a complete copy of a smaller edition, a copy short a
# track, a set missing a disc, a set no folder name ever spelled out.


def _shaped(*entries):
    """A presence index from (title, artist, year, tracks, folder, extra)
    tuples, where ``extra`` carries the declared shape the scanner read off the
    files: ``declared``, ``disc_no``, ``disc_total``."""
    idx: dict = {}
    for title, artist, year, tracks, fp, extra in entries:
        idx.setdefault(_presence_key(title, artist), []).append(
            {"title": title, "year": year, "tracks": tracks, "id": fp, **extra}
        )
    return idx


def test_a_copy_short_of_its_own_declared_count_is_not_complete():
    # TIDAL's edition has 10 tracks and the folder holds 10 files, which used to
    # be the whole test. But the files themselves say the release has 12, so two
    # of what is on disk are something else (a bonus rip, a stray single) and
    # the album is not all here. The button stays live.
    idx = _shaped(("Album", "A", "2019", 10, "/m/A/Album", {"declared": 12}))
    r = _decide_presence("Album", "A", "2019", 10, idx)
    assert r["present"] is True
    assert r["full"] is False and r["partial"] is True


def test_a_release_declaring_fewer_tracks_is_not_the_edition_on_screen():
    # Title and year agree perfectly, so identity used to be proven. The local
    # release says it holds 10 tracks and the one being viewed has 11: these are
    # different releases, and the badge wears its "?" again.
    idx = _shaped(("Album", "A", "2017", 10, "/m/A/Album", {"declared": 10}))
    r = _decide_presence("Album", "A", "2017", 11, idx)
    assert r["present"] is True
    assert r["sure"] is False and r["full"] is False


def test_a_richer_release_still_proves_the_one_on_screen():
    # Only a SHORTFALL rules identity out. A deluxe copy declaring 15 contains
    # the standard 12 being viewed, so it stays proven and complete: ruling this
    # out would cost the user a green badge on an album they own twice over.
    idx = _shaped(("Album", "A", "2019", 15, "/m/A/Album", {"declared": 15}))
    r = _decide_presence("Album", "A", "2019", 12, idx)
    assert r["sure"] is True and r["full"] is True and r["partial"] is False


def test_coverage_is_provable_when_tidal_reports_no_track_count():
    # tidalapi hands back None/-1 for numberOfTracks often enough to matter, and
    # with nothing to compare against a complete album read "partially in
    # library" forever. The release's own claim settles it without TIDAL.
    idx = _shaped(("Album", "A", "2019", 12, "/m/A/Album", {"declared": 12}))
    r = _decide_presence("Album", "A", "2019", 0, idx)
    assert r["sure"] is True and r["full"] is True and r["partial"] is False


def test_a_silent_library_without_tidals_count_still_stays_partial():
    # The same album with no tracktotal on its files: nothing can prove
    # coverage, so the verdict is exactly what it was before this evidence
    # existed. A missing claim never becomes a claim.
    idx = _shaped(("Album", "A", "2019", 12, "/m/A/Album", {}))
    r = _decide_presence("Album", "A", "2019", 0, idx)
    assert r["present"] is True and r["full"] is False


def test_an_unproven_match_is_never_completed_by_its_own_claim():
    # Coverage from the local side is only worth anything once identity is
    # settled: an undated folder matches every same-titled album, and letting it
    # certify itself complete would hand the strictest bar in the matcher to the
    # loosest evidence in it.
    idx = _shaped(("Album", "A", "", 12, "/m/A/Album", {"declared": 12}))
    r = _decide_presence("Album", "A", "2019", 0, idx)
    assert r["sure"] is False and r["full"] is False


def test_disc_tags_join_a_set_no_folder_name_spells_out():
    # "Album/Reprise" is disc 2 of the record and no marker says so. The files
    # know, and this is the whole point of reading them: 9 OF 18 becomes a
    # complete copy.
    idx = _shaped(
        ("Album", "A", "2019", 9, "/m/A/Album/Origins", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2019", 9, "/m/A/Album/Reprise", {"disc_no": 2, "disc_total": 2}),
    )
    r = _decide_presence("Album", "A", "2019", 18, idx)
    assert r["local_tracks"] == 18 and r["partial"] is False


def test_two_folders_declaring_the_same_disc_are_never_summed():
    # Two rips of disc 1, neither folder named for a disc. The tags catch what
    # the folder names could not even see, and summing them would claim a
    # complete copy of an album the user owns half of, twice.
    idx = _shaped(
        ("Album", "A", "2019", 9, "/m/A/Album/rip", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2019", 9, "/m/A/Album/rip_1", {"disc_no": 1, "disc_total": 2}),
    )
    r = _decide_presence("Album", "A", "2019", 18, idx)
    assert r["local_tracks"] == 9 and r["partial"] is True


def test_a_set_short_of_the_discs_it_declares_is_refused():
    # Two folders of a THREE disc set, and the folder names group them happily.
    # The tags are the only thing that knows a third of the record is missing.
    idx = _shaped(
        ("Album", "A", "2019", 9, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 3}),
        ("Album", "A", "2019", 9, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 3}),
    )
    r = _decide_presence("Album", "A", "2019", 18, idx)
    assert r["local_tracks"] == 9 and r["partial"] is True


def test_disc_tags_never_reach_across_folders_or_pressings():
    # Discs of one set live together. Two copies filed under different parents,
    # or carrying different years, are two different pressings and adding a disc
    # of each together invents a set that is on no shelf.
    apart = _shaped(
        ("Album", "A", "2019", 9, "/m/A/Album", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2019", 9, "/m/B/Album", {"disc_no": 2, "disc_total": 2}),
    )
    assert _decide_presence("Album", "A", "2019", 18, apart)["local_tracks"] == 9
    pressings = _shaped(
        ("Album", "A", "2019", 9, "/m/A/one", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2004", 9, "/m/A/two", {"disc_no": 2, "disc_total": 2}),
    )
    assert _decide_presence("Album", "A", "2019", 18, pressings)["local_tracks"] == 9


def test_a_joined_set_needs_every_disc_to_declare_its_count():
    # The declared total of a set is the SUM, and a sum missing one term is an
    # undercount. An undercount is precisely what would call a short copy
    # complete, so one silent disc drops the claim entirely rather than
    # shrinking it.
    idx = _shaped(
        ("Album", "A", "2019", 9, "/m/A/Album (Disc 1)", {"declared": 9, "disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2019", 8, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 2}),
    )
    r = _decide_presence("Album", "A", "2019", 0, idx)
    assert r["local_tracks"] == 17 and r["full"] is False


# --- Track presence (decide_track_presence) ------------------------------------


def _tindex(*entries):
    """A track index from (title, artist, facts) triples, keyed like the bridge
    builds it."""
    from tidaler.matching import track_key

    idx: dict = {}
    for title, artist, facts in entries:
        idx.setdefault(track_key(title, artist), []).append({"id": facts.get("id", "/lib/A/Alb"), **facts})
    return idx


def test_track_present_on_exact_normalised_match():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Kill or Be Killed", "Muse", {"codec": "flac"}))
    got = decide_track_presence("Kill or Be Killed", "Muse", idx)
    assert got["present"] is True
    assert got["local_album_id"] == "/lib/A/Alb"
    assert got["local_class"] == "lossless"


def test_track_curly_apostrophe_still_matches():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Don't Stop Me Now", "Queen", {}))
    assert decide_track_presence("Don’t Stop Me Now", "Queen", idx)["present"] is True


def test_track_explicit_marker_folds_but_edition_does_not():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Song", "A", {}))
    # (Explicit) folds away, exactly as albums do.
    assert decide_track_presence("Song (Explicit)", "A", idx)["present"] is True
    # An edition qualifier is a DIFFERENT recording and must not match.
    assert decide_track_presence("Song (Acoustic)", "A", idx)["present"] is False
    assert decide_track_presence("Song (Live)", "A", idx)["present"] is False


def test_track_requires_the_artist_to_match():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Intro", "The xx", {}))
    assert decide_track_presence("Intro", "Alt-J", idx)["present"] is False


def test_track_refuses_empty_and_various_artists():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Song", "Various Artists", {}), ("Song", "", {}))
    assert decide_track_presence("Song", "", idx)["present"] is False
    assert decide_track_presence("Song", "Various Artists", idx)["present"] is False


def test_track_empty_title_matches_nothing():
    from tidaler.matching import decide_track_presence

    assert decide_track_presence("", "A", _tindex(("", "A", {})))["present"] is False


def test_track_best_quality_copy_wins():
    from tidaler.matching import decide_track_presence

    idx = _tindex(
        ("Song", "A", {"id": "/lib/lossy", "codec": "mp3", "bitrate": 128}),
        ("Song", "A", {"id": "/lib/lossless", "codec": "flac"}),
    )
    got = decide_track_presence("Song", "A", idx)
    assert got["local_album_id"] == "/lib/lossless"
    assert got["local_class"] == "lossless"


def test_track_unbuilt_index_hides():
    from tidaler.matching import decide_track_presence

    assert decide_track_presence("Song", "A", {})["present"] is False
    assert decide_track_presence("Song", "A", None)["present"] is False


def test_non_numeric_quality_facts_cost_a_readout_not_a_crash():
    # Index rows normally carry ints from the scanner, but they also cross a
    # bridge boundary and can come out of older cache files, so a stray string
    # in a quality field must degrade to an empty readout, never ValueError
    # inside a badge resolve (which the Worker wrapper would swallow, leaving
    # every badge silently blank).
    from tidaler.matching import decide_presence, decide_track_presence

    idx = _tindex(("Song", "A", {"codec": "mp3", "bitrate": "320kbps", "bits": "", "rate": None}))
    got = decide_track_presence("Song", "A", idx)
    assert got["present"] is True
    assert got["local_class"] == "high"  # unknown bitrate reads high, benefit of the doubt

    aidx = _index(("Album", "A", "2000", 10, "/lib/A/Album"))
    aidx[next(iter(aidx))][0]["bitrate"] = "320kbps"
    aidx[next(iter(aidx))][0]["bits"] = "n/a"
    got = decide_presence("Album", "A", "2000", 10, aidx)
    assert got["present"] is True


# --- Track identity: the proof a track inherits from its folder ---------------
# A track carries no year of its own and its title plus artist match every
# edition, live take and compilation that share them, so the album folder it
# was found in is the only evidence a track match can offer. These pin that the
# axis behaves exactly like the album one, and that a missing answer reads
# unproven rather than proven.


def test_track_inherits_its_folders_proof_when_the_caller_names_the_album():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Crawl", "Miss May I", {"codec": "flac", "album": "Shadows Inside", "album_year": "2017"}))
    assert decide_track_presence("Crawl", "Miss May I", idx, "Shadows Inside", "2017")["sure"] is True
    assert decide_track_presence("Crawl", "Miss May I", idx, "Shadows Inside", "2018")["sure"] is True  # within one


def test_track_without_album_context_is_present_but_never_proven():
    # The two-argument callers (the bulk claim gate, any row that cannot name
    # its album) must keep the hedge: presence is still reported.
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Crawl", "Miss May I", {"codec": "flac", "album": "Shadows Inside", "album_year": "2017"}))
    got = decide_track_presence("Crawl", "Miss May I", idx)
    assert got["present"] is True
    assert got["sure"] is False


def test_track_proof_refuses_a_disagreeing_year_or_edition():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Crawl", "Miss May I", {"codec": "flac", "album": "Shadows Inside", "album_year": "2017"}))
    assert decide_track_presence("Crawl", "Miss May I", idx, "Shadows Inside", "2011")["sure"] is False
    assert decide_track_presence("Crawl", "Miss May I", idx, "Rise of the Lion", "2017")["sure"] is False
    # An undated folder proves nothing, the same bar the album verdict applies.
    undated = _tindex(("Crawl", "Miss May I", {"codec": "flac", "album": "Shadows Inside", "album_year": ""}))
    assert decide_track_presence("Crawl", "Miss May I", undated, "Shadows Inside", "2017")["sure"] is False
    # An edition qualifier is part of the identity, as it is for albums.
    deluxe = _tindex(
        ("Crawl", "Miss May I", {"codec": "flac", "album": "Shadows Inside (Deluxe)", "album_year": "2017"})
    )
    assert decide_track_presence("Crawl", "Miss May I", deluxe, "Shadows Inside", "2017")["sure"] is False


def test_a_proven_copy_outranks_a_better_sounding_stranger():
    # The album copy and a loose single share the title. Quality alone would
    # pick the single (higher bitrate) and report the whole match unproven,
    # which is precisely backwards: the proven copy is the one the user means,
    # so identity picks the candidate and quality only breaks ties within it.
    from tidaler.matching import decide_track_presence

    idx = _tindex(
        (
            "Crawl",
            "Miss May I",
            {"codec": "flac", "bitrate": 900, "id": "/lib/album", "album": "Shadows Inside", "album_year": "2017"},
        ),
        (
            "Crawl",
            "Miss May I",
            {"codec": "flac", "bitrate": 1400, "id": "/lib/single", "album": "Crawl", "album_year": "2017"},
        ),
    )
    got = decide_track_presence("Crawl", "Miss May I", idx, "Shadows Inside", "2017")
    assert got["sure"] is True
    assert got["local_album_id"] == "/lib/album"
    # With no album named, the old quality-only choice still stands.
    assert decide_track_presence("Crawl", "Miss May I", idx)["local_album_id"] == "/lib/single"


# --- The edition detector (same_edition) ---------------------------------------
# gate_title compares qualifiers verbatim, which fails every cross-catalog
# spelling of ONE edition: measured against a real 11k-album library, "Deluxe" /
# "Deluxe Edition" / "Deluxe Version" were 274 rows of the same thing, and
# "(2011 Remaster)" and "(Remastered 2011)" the same master twice. same_edition
# folds recognised edition vocabulary into a tag set so those spellings agree,
# and refuses everything else: an unknown word keeps the whole tail literal,
# classes never collapse into each other, and remaster years must agree when
# both sides carry one. Every widening here is a synonym, never an edition.


def test_one_edition_in_different_coats_agrees():
    assert _same_edition("Album (Deluxe)", "Album (Deluxe Edition)")
    assert _same_edition("Album (Deluxe Version)", "Album (Deluxe Edition)")
    assert _same_edition("Album (2011 Remaster)", "Album (Remastered 2011)")
    assert _same_edition("Album (Remastered)", "Album (2011 Remaster)")
    assert _same_edition("Album (Acoustic)", "Album (Acoustic Version)")
    assert _same_edition("Album (Instrumentals)", "Album (Instrumental Version)")
    assert _same_edition("Album (20th Anniversary)", "Album (20th Anniversary Edition)")
    # The dash coat some taggers use where TIDAL writes a parenthetical.
    assert _same_edition("Album - Deluxe Edition", "Album (Deluxe)")
    # Combined qualifiers describe one release however they punctuate.
    assert _same_edition("Album (Deluxe Edition/Remastered)", "Album (Remastered Deluxe Version)")


def test_different_editions_never_agree():
    assert not _same_edition("Album (Deluxe)", "Album")
    assert not _same_edition("Album (Super Deluxe)", "Album (Deluxe)")
    assert not _same_edition("Album (Expanded Edition)", "Album (Deluxe Edition)")
    assert not _same_edition("Album (Acoustic)", "Album")
    assert not _same_edition("Album (Live)", "Album")
    assert not _same_edition("Album (2009 Remaster)", "Album (2015 Remaster)")
    assert not _same_edition("Album (20th Anniversary)", "Album (25th Anniversary)")
    assert not _same_edition("Album (Bonus Track Version)", "Album")


def test_unknown_words_keep_a_tail_literal():
    # The Sault case that shaped gate_title: recognition may fold spellings of
    # one thing, never two different things.
    assert not _same_edition("Untitled (Black Is)", "Untitled (Rise)")
    assert _same_edition("Untitled (Black Is)", "Untitled (Black Is)")
    # A mixed tail (one unknown segment) stays literal as a whole.
    assert not _same_edition("Album (White Album / Super Deluxe)", "Album (Super Deluxe)")
    # Years or filler alone name something this vocabulary cannot identify.
    assert not _same_edition("Album (2015 Edition)", "Album")
    assert not _same_edition("Live - 1970", "Live")
    # A remix names a different recording and is deliberately NOT vocabulary.
    assert not _same_edition("Album (Kygo Remix)", "Album (Remix)")


def test_edition_key_reads_the_tail_into_tags():
    base, tags, years = _edition_key("Album (Deluxe Edition/Remastered)")
    assert base == "album"
    assert tags == frozenset({"deluxe", "remaster"})
    assert years == frozenset()
    base, tags, years = _edition_key("Album (2011 Remaster)")
    assert tags == frozenset({"remaster"}) and years == frozenset({2011})
    # Unknown tail: nothing peeled, nothing tagged.
    base, tags, years = _edition_key("Untitled (Black Is)")
    assert base == "untitled (black is)" and tags == frozenset()


def test_cross_spelled_edition_is_proven_end_to_end():
    # The whole point, through decide_presence: the local tagger wrote "Deluxe
    # Edition", TIDAL shows "Deluxe", and the match is proven instead of
    # hedging gold forever.
    idx = _index(("Album (Deluxe Edition)", "A", "2019", 15, "/m/A/AlbumDX"))
    r = _decide_presence("Album (Deluxe)", "A", "2019", 15, idx)
    assert r["present"] is True and r["sure"] is True and r["full"] is True


def test_cross_spelled_remaster_year_must_agree_end_to_end():
    idx = _index(("Album (2009 Remaster)", "A", "2009", 12, "/m/A/Album09"))
    ok = _decide_presence("Album (Remastered 2009)", "A", "2009", 12, idx)
    assert ok["sure"] is True
    other = _decide_presence("Album (Remastered 2010)", "A", "2009", 12, idx)
    assert other["sure"] is False


def test_track_proof_crosses_the_same_spellings():
    from tidaler.matching import decide_track_presence, track_key

    # The holding folder says "Deluxe Edition", the caller's album says
    # "Deluxe": the track's identity is still proven by its folder.
    idx = {
        track_key("Song", "Artist"): [
            {
                "id": "/lib/deluxe",
                "codec": "flac",
                "bitrate": 0,
                "bits": 16,
                "rate": 44100,
                "album": "Album (Deluxe Edition)",
                "album_year": "2019",
            }
        ]
    }
    got = decide_track_presence("Song", "Artist", idx, "Album (Deluxe)", "2019")
    assert got["sure"] is True
    # A different edition still refuses.
    assert decide_track_presence("Song", "Artist", idx, "Album", "2019")["sure"] is False


# ---- The duration witness ----------------------------------------------------
# Play length is the identity fact no tag has to carry: the files themselves
# know it. Summed over a folder holding EXACTLY the tracks on screen it proves
# an undated match (each track granted 2 seconds, the bar the lyrics client has
# matched LRCLIB candidates on), and a same-count copy minutes away is refuted.
# A superset copy (deluxe holding the standard) never testifies either way.


def _didx(*entries):
    """An album index whose rows carry a runtime (summed seconds)."""
    idx: dict = {}
    for title, artist, year, tracks, fp, runtime in entries:
        idx.setdefault(_presence_key(title, artist), []).append(
            {"title": title, "year": year, "tracks": tracks, "id": fp, "runtime": runtime}
        )
    return idx


def test_runtime_proves_an_undated_match():
    # No year on either side used to be a forever-gold pill; 12 tracks whose
    # every second matches are that release.
    idx = _didx(("Album", "Artist", "", 12, "fp", 2400))
    r = _decide_presence("Album", "Artist", "", 12, idx, 2400)
    assert r["present"] and r["sure"] is True


def test_runtime_proof_respects_the_per_track_tolerance():
    idx = _didx(("Album", "Artist", "", 12, "fp", 2400))
    assert _decide_presence("Album", "Artist", "", 12, idx, 2424)["sure"] is True  # 24s = 12x2s, at the bar
    assert _decide_presence("Album", "Artist", "", 12, idx, 2430)["sure"] is False  # past it: unproven, not refuted


def test_runtime_without_count_parity_never_testifies():
    # A 14-track deluxe copy naturally runs longer than the 12-track standard on
    # screen: no proof (different count) but also no refutation.
    idx = _didx(("Album", "Artist", "2020", 14, "fp", 3100))
    r = _decide_presence("Album", "Artist", "2020", 12, idx, 2400)
    assert r["present"] and r["sure"] is True  # years still prove; the surplus runtime does not unswear


def test_runtime_refutes_a_same_count_impostor():
    # Years and count agree, but ten minutes of disagreement is a different
    # recording wearing the same name.
    idx = _didx(("Album", "Artist", "2020", 12, "fp", 3000))
    r = _decide_presence("Album", "Artist", "2020", 12, idx, 2400)
    assert r["present"] and r["sure"] is False


def test_runtime_disagreement_between_bars_is_neutral():
    # Between proof (24s) and refutation (72s): years keep the verdict.
    idx = _didx(("Album", "Artist", "2020", 12, "fp", 2450))
    assert _decide_presence("Album", "Artist", "2020", 12, idx, 2400)["sure"] is True


def test_unspoken_runtime_is_never_evidence():
    # 0 means "the files never said": no proof, no refutation.
    idx = _didx(("Album", "Artist", "", 12, "fp", 0))
    assert _decide_presence("Album", "Artist", "", 12, idx, 2400)["sure"] is False
    idx2 = _didx(("Album", "Artist", "2020", 12, "fp", 0))
    assert _decide_presence("Album", "Artist", "2020", 12, idx2, 2400)["sure"] is True


def test_no_duration_from_caller_changes_nothing():
    # The bulk gate calls with no duration; the verdict must be the pre-witness one.
    idx = _didx(("Album", "Artist", "2020", 12, "fp", 3000))
    assert _decide_presence("Album", "Artist", "2020", 12, idx)["sure"] is True


def test_length_vouches_for_a_remaster_wearing_the_original_year():
    # Remasters are routinely tagged with the ORIGINAL release's year: 1985 on
    # disk beside TIDAL's 2011 reissue. The year gate used to hide the match
    # entirely; agreeing count + seconds now outrank the year and prove it.
    idx = _didx(("Album", "Artist", "1985", 12, "fp", 2400))
    r = _decide_presence("Album", "Artist", "2011", 12, idx, 2400)
    assert r["present"] is True and r["sure"] is True and r["local_album_id"] == "fp"


def test_year_still_rejects_when_no_length_vouches():
    # Without durations on BOTH sides the year stays the only separating fact
    # (a self-titled series), so a disagreement beyond one still hides the match.
    idx = _didx(("Album", "Artist", "1985", 12, "fp", 0))
    assert _decide_presence("Album", "Artist", "2011", 12, idx, 2400)["present"] is False
    idx2 = _didx(("Album", "Artist", "1985", 12, "fp", 2400))
    assert _decide_presence("Album", "Artist", "2011", 12, idx2, 0)["present"] is False


def test_wrong_length_never_vouches_past_the_year():
    # The colour-LP case with durations on record: disagreeing year AND seconds
    # whole songs apart is a different album, hidden as before; an ambiguous
    # length (past the proof bar) does not vouch either.
    idx = _didx(("Weezer", "Weezer", "1994", 10, "blue", 2495))
    assert _decide_presence("Weezer", "Weezer", "2001", 10, idx, 1710)["present"] is False
    idx2 = _didx(("Album", "Artist", "1985", 12, "fp", 2450))
    assert _decide_presence("Album", "Artist", "2011", 12, idx2, 2400)["present"] is False


def test_length_outranks_a_lying_year_on_a_multi_disc_set():
    # The remaster-wearing-the-original-year rescue, for a SET: the year veto
    # runs per candidate, and one disc can never vouch alone (its own count
    # and seconds are half the record), so every disc of a 1985-tagged
    # two-disc remaster was vetoed and the join never happened. The joined
    # set's summed count and seconds are what must answer.
    idx = _shaped(
        ("Album", "Artist", "1985", 9, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 2, "runtime": 1200}),
        ("Album", "Artist", "1985", 9, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 2, "runtime": 1200}),
    )
    r = _decide_presence("Album", "Artist", "2011", 18, idx, 2400)
    assert r["present"] is True and r["sure"] is True and r["full"] is True


def test_a_multi_disc_set_without_durations_keeps_the_year_veto():
    # The Weezer safety holds for sets too: with no seconds on record the year
    # stays the only separating fact, joined or not, and must keep rejecting.
    idx = _shaped(
        ("Album", "Artist", "1985", 9, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 2}),
        ("Album", "Artist", "1985", 9, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 2}),
    )
    assert _decide_presence("Album", "Artist", "2011", 18, idx, 2400)["present"] is False
    # And a set whose summed seconds sit whole songs away never vouches past
    # the year either: that is a different recording wearing the same name.
    idx2 = _shaped(
        ("Album", "Artist", "1985", 9, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 2, "runtime": 900}),
        ("Album", "Artist", "1985", 9, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 2, "runtime": 900}),
    )
    assert _decide_presence("Album", "Artist", "2011", 18, idx2, 2400)["present"] is False


def test_punctuation_only_titles_never_cross_claim():
    # Every title made solely of strippable punctuation normalises to "" and
    # shares one artist-only key AND an empty gate_title, so a local "-" could
    # fully claim a screen "...": two different releases. The album side now
    # refuses an empty-normalised title outright, like the track side always
    # has. (A title with any surviving character, XXXTENTACION's "?", still
    # matches normally.)
    idx = _index(("-", "Artist", "2019", 10, "fp"))
    assert _decide_presence("...", "Artist", "2019", 10, idx)["present"] is False
    assert _decide_presence("-", "Artist", "2019", 10, idx)["present"] is False


def test_artist_rollup_sums_a_sets_discs_and_dedups_editions():
    from tidaler.matching import build_artist_rollup

    # One folder per disc, one bucket per album: max() alone halved a double
    # album to 9, while a duplicate copy of one disc must still count once
    # and a bigger single-folder copy outbids the sum.
    artist_key = _presence_key("Album", "Artist")[1]
    idx = _shaped(
        ("Album", "Artist", "2019", 9, "/m/A/Album/CD1", {"disc_no": 1, "disc_total": 2}),
        ("Album", "Artist", "2019", 9, "/m/A/Album/CD2", {"disc_no": 2, "disc_total": 2}),
    )
    roll = build_artist_rollup(idx)
    assert roll[artist_key]["albums"] == 1 and roll[artist_key]["tracks"] == 18
    idx2 = _shaped(
        ("Album", "Artist", "2019", 9, "/m/A/Album/CD1", {"disc_no": 1, "disc_total": 2}),
        ("Album", "Artist", "2019", 9, "/m/A/rip/CD1", {"disc_no": 1, "disc_total": 2}),
        ("Album", "Artist", "2019", 9, "/m/A/Album/CD2", {"disc_no": 2, "disc_total": 2}),
        ("Album (Super Deluxe)", "Artist", "2019", 20, "/m/A/Deluxe", {}),
    )
    roll2 = build_artist_rollup(idx2)
    assert roll2[artist_key]["albums"] == 1 and roll2[artist_key]["tracks"] == 20


def test_track_length_never_proves_on_its_own():
    # Seconds name a RECORDING, and every compilation, best-of and re-release
    # carries the same recording to the second, so matching length can never
    # stand in for the album the caller asked about (issue #24).
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Song", "A", {"length": 200}))
    assert decide_track_presence("Song", "A", idx, duration=201)["present"] is True
    assert decide_track_presence("Song", "A", idx, duration=201)["sure"] is False


def test_track_length_refutes_folder_proof():
    from tidaler.matching import decide_track_presence

    # The folder swears but the file is a minute short: a different recording.
    facts = {"length": 140, "album": "Album", "album_year": "2020"}
    idx = _tindex(("Song", "A", {**facts}))
    assert decide_track_presence("Song", "A", idx, "Album", "2020", 200)["sure"] is False
    # In the neutral band (under 5x the bar) the folder's word still stands.
    idx2 = _tindex(("Song", "A", {**facts, "length": 195}))
    assert decide_track_presence("Song", "A", idx2, "Album", "2020", 200)["sure"] is True


def test_track_without_length_keeps_folder_proof():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Song", "A", {"album": "Album", "album_year": "2020"}))
    assert decide_track_presence("Song", "A", idx, "Album", "2020", 200)["sure"] is True
    assert decide_track_presence("Song", "A", idx, "Album", "2020")["sure"] is True


def test_a_copy_filed_under_another_album_is_reported_but_never_proven():
    """Issue #24: the compilation case, which is the common case.

    A studio album is on disk and the user opens a best-of that reuses its
    recordings. Every track matches on title, artist AND seconds, because it
    is literally the same master. That is worth SAYING (the pill points at
    the copy they have), but it is not this release, and the bulk claim gate
    rides the proven axis: reporting it proven skipped those tracks out of the
    best-of, which then landed as a folder with holes and no word of it.
    """
    from tidaler.matching import decide_track_presence

    true = {"id": "/lib/Avicii/True", "album": "True", "album_year": "2013", "codec": "flac", "length": 247}
    idx = _tindex(("Wake Me Up", "Avicii", true))

    # The best-of: same recording to the second, different release.
    best_of = decide_track_presence("Wake Me Up", "Avicii", idx, "Avicii Forever", "2024", 247)
    assert best_of["present"] is True, "the copy they hold is still worth pointing at"
    assert best_of["sure"] is False, "a copy filed elsewhere may not answer for this release"
    assert best_of["local_album_id"] == "/lib/Avicii/True", "and the pill still names where it is"

    # The album it really belongs to: proven, and the gate may skip it.
    home = decide_track_presence("Wake Me Up", "Avicii", idx, "True", "2013", 247)
    assert home["present"] is True and home["sure"] is True


def test_disc_set_runtime_is_its_discs_summed():
    # An undated two-disc set whose summed seconds match the release on screen
    # is that release; a set with one silent disc never testifies.
    idx = _didx(
        ("Big Album", "A", "", 9, "/m/A/Big Album/CD1", 2000),
        ("Big Album", "A", "", 9, "/m/A/Big Album/CD2", 2200),
    )
    r = _decide_presence("Big Album", "A", "", 18, idx, 4200)
    assert r["local_tracks"] == 18 and r["sure"] is True
    idx2 = _didx(
        ("Big Album", "A", "", 9, "/m/A/Big Album/CD1", 2000),
        ("Big Album", "A", "", 9, "/m/A/Big Album/CD2", 0),
    )
    assert _decide_presence("Big Album", "A", "", 18, idx2, 4200)["sure"] is False


# ---- hardening: year parsing and count coercion -----------------------------
def test_year_parses_from_anywhere_in_string():
    from tidaler.matching import to_year_int

    assert to_year_int("1999") == 1999
    assert to_year_int("1999-03-01") == 1999
    assert to_year_int("01/03/1999") == 1999
    assert to_year_int("(p) 2004") == 2004
    assert to_year_int(1985) == 1985


def test_two_digit_year_reads_none_and_never_rejects():
    from tidaler.matching import to_year_int

    # "97" used to parse as the year 97, an int that actively rejected every
    # candidate; an untrustworthy year must read as absent instead.
    assert to_year_int("97") is None
    idx = _index(("Album", "Artist", "97", 12, "rk"))
    assert _decide_presence("Album", "Artist", "1997", 12, idx)["present"] is True


def test_five_digit_run_is_not_a_year():
    from tidaler.matching import to_year_int

    assert to_year_int("12019") is None
    assert to_year_int("20191") is None


def test_year_zero_and_none_read_none():
    from tidaler.matching import to_year_int

    assert to_year_int(None) is None
    assert to_year_int("") is None
    assert to_year_int(0) is None


def test_track_count_string_garbage_does_not_raise():
    idx = _index(("Album", "Artist", "2020", 12, "rk"))
    r = _decide_presence("Album", "Artist", "2020", "12 tracks", idx)
    # Garbage reads as "TIDAL never said": present and proven by year, but
    # coverage cannot be settled without a count to reach.
    assert r["present"] is True and r["full"] is False


# ---- bucket canonicalisation: &/+, bare special, ordinals -------------------
def test_ampersand_and_plus_fold_to_and_in_titles_and_artists():
    assert _presence_key("Love & Hate", "Simon & Garfunkel") == _presence_key("Love and Hate", "Simon and Garfunkel")
    assert _presence_key("Album", "Florence + The Machine") == _presence_key("Album", "Florence and The Machine")
    idx = _index(("Love & Hate", "Simon & Garfunkel", "2016", 10, "fp"))
    assert _decide_presence("Love and Hate", "Simon and Garfunkel", "2016", 10, idx)["present"] is True


def test_glued_ampersand_and_plus_untouched():
    # No whitespace, no fold: these names spell their punctuation on purpose.
    assert _presence_key("Album", "AC/DC") != _presence_key("Album", "AC and DC")
    assert _presence_key("Album", "AT&T") != _presence_key("Album", "AT and T")
    assert _presence_key("1+1", "Artist") != _presence_key("1 and 1", "Artist")
    assert _presence_key("+", "Ed Sheeran") != _presence_key("and", "Ed Sheeran")


def test_edition_tail_with_ampersand_parses_after_fold():
    # "(Deluxe & Bonus)" used to stay a literal tail (the tail splitter never
    # split on "&"); folded to "and" it is filler and the tail parses.
    assert _same_edition("Album (Deluxe & Bonus)", "Album (Deluxe and Bonus)")
    assert _same_edition("Album (Deluxe & Bonus)", "Album (Bonus Deluxe)")


def test_bare_special_and_special_edition_share_a_bucket():
    assert _presence_key("Album (Special)", "A") == _presence_key("Album (Special Edition)", "A")
    idx = _index(("Album (Special)", "A", "2020", 12, "fp"))
    r = _decide_presence("Album (Special Edition)", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is True


def test_unparseable_special_phrase_keeps_todays_collapse():
    # A tail the detector cannot parse is peeled from the key as before, so
    # "Album (A Special Something)" still finds the plain album's bucket and
    # still never gates it.
    assert _presence_key("Album (A Special Something)", "A") == _presence_key("Album", "A")
    idx = _index(("Album", "A", "2020", 12, "fp"))
    r = _decide_presence("Album (A Special Something)", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is False


def test_special_stays_a_distinct_release_group():
    # Keep-class, like a remaster: a special edition files in its own bucket
    # and never lights the plain album's pill in either direction.
    idx = _index(("Album (Special)", "A", "2020", 12, "fp"))
    assert _decide_presence("Album", "A", "2020", 12, idx)["present"] is False
    idx2 = _index(("Album", "A", "2020", 12, "fp"))
    assert _decide_presence("Album (Special)", "A", "2020", 12, idx2)["present"] is False


def test_anniversary_ordinal_variants_share_a_presence_bucket():
    assert _presence_key("Album (20th Anniversary Edition)", "A") == _presence_key("Album (Anniversary Edition)", "A")
    idx = _index(("Album (Anniversary Edition)", "A", "2020", 12, "fp"))
    r = _decide_presence("Album (20th Anniversary Edition)", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is False  # tag sets differ: never gates


def test_twentieth_vs_twenty_fifth_anniversary_never_gate_each_other():
    assert _presence_key("Album (20th Anniversary)", "A") == _presence_key("Album (25th Anniversary)", "A")
    idx = _index(("Album (20th Anniversary)", "A", "2020", 12, "fp"))
    r = _decide_presence("Album (25th Anniversary)", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is False


def test_bare_ordinal_tail_buckets_with_plain_album_but_never_gates():
    assert _presence_key("Album (30th)", "A") == _presence_key("Album", "A")
    idx = _index(("Album", "A", "2020", 12, "fp"))
    r = _decide_presence("Album (30th)", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is False


# ---- diacritic folding: Latin accents strip, other scripts untouched --------
@pytest.mark.parametrize(
    "marked,plain",
    [
        ("Björk", "Bjork"),
        ("Motörhead", "Motorhead"),
        ("Beyoncé", "Beyonce"),
        ("Sigur Rós", "Sigur Ros"),
    ],
)
def test_canon_folds_latin_diacritics(marked, plain):
    assert _canon(marked) == plain


@pytest.mark.parametrize(
    "special,plain",
    [
        ("MØ", "MO"),
        ("Łukasz", "Lukasz"),
        ("Đorđe", "Dorde"),
        ("Encyclopædia", "Encyclopaedia"),
        ("Straße", "Strasse"),
        ("Œuvre", "OEuvre"),
    ],
)
def test_canon_folds_non_decomposing_letters(special, plain):
    assert _canon(special) == plain


def test_canon_leaves_kana_voicing_marks_alone():
    # Dakuten are combining marks too; a naive strip folds バ into ハ and
    # ヴ into ウ, colliding different kana and breaking the Various-Artists
    # marker. The Latin guard keeps every non-Latin mark where it was.
    assert _canon("バラード") == "バラード"
    assert _canon("ヴァリアス・アーティスト") == "ヴァリアス・アーティスト"
    assert _presence_key("ハート", "A") != _presence_key("バート", "A")


def test_various_artists_marker_survives_diacritic_fold():
    from tidaler.matching import is_various_artists

    assert is_various_artists(_canon("ヴァリアス・アーティスト"))


def test_presence_matches_across_diacritic_spellings():
    idx = _index(("Post", "Björk", "1995", 11, "fp"))
    r = _decide_presence("Post", "Bjork", "1995", 11, idx)
    assert r["present"] is True and r["sure"] is True
    idx2 = _index(("Vespertine", "Bjork", "2001", 12, "fp"))
    assert _decide_presence("Vespertine", "Björk", "2001", 12, idx2)["present"] is True


# ---- artist-side folds: separators and the leading The ----------------------
def test_norm_artist_takes_first_semicolon_segment():
    from tidaler.matching import norm_artist

    assert norm_artist("Artist; Guest") == "artist"
    assert norm_artist("Artist;Guest") == "artist"
    idx = _index(("Album", "Artist; Guest", "2020", 12, "fp"))
    assert _decide_presence("Album", "Artist", "2020", 12, idx)["present"] is True


def test_norm_artist_slash_splits_only_with_spaces():
    from tidaler.matching import norm_artist

    assert norm_artist("Artist / Guest") == "artist"
    assert norm_artist("AC/DC") == "ac/dc"
    assert norm_artist("GZA/Genius") == "gza/genius"


def test_norm_artist_never_splits_on_comma():
    from tidaler.matching import norm_artist

    assert norm_artist("Earth, Wind & Fire") == "earth, wind and fire"


def test_norm_artist_split_never_yields_an_empty_key():
    from tidaler.matching import norm_artist

    # An empty head keeps the original text whole: a degenerate credit must
    # never collapse into the empty artist, which would key on title alone.
    assert norm_artist("; Guest") == "; guest"
    assert norm_artist(";") == ";"


def test_the_prefix_folds_on_artists_only():
    from tidaler.matching import norm_artist

    assert norm_artist("The Beatles") == "beatles"
    assert _presence_key("Abbey Road", "The Beatles") == _presence_key("Abbey Road", "Beatles")
    # Titles never fold: The Wall stays its own album.
    assert _presence_key("The Wall", "Pink Floyd") != _presence_key("Wall", "Pink Floyd")


def test_the_the_never_folds_to_empty():
    from tidaler.matching import norm_artist

    assert norm_artist("The The") == "the"
    assert norm_artist("The") == "the"


def test_collab_tagged_folder_matches_main_artist_album():
    idx = _index(("Watch the Throne", "JAY-Z; Kanye West", "2011", 12, "fp"))
    r = _decide_presence("Watch the Throne", "JAY-Z", "2011", 12, idx)
    assert r["present"] is True and r["sure"] is True


# ---- featuring credits: stripped from the key, kept as gate evidence --------
def test_track_key_strips_feat_from_artist_and_title():
    from tidaler.matching import track_key

    plain = track_key("Song", "A")
    assert track_key("Song (feat. B)", "A") == plain
    assert track_key("Song [ft. B]", "A") == plain
    assert track_key("Song (featuring B)", "A") == plain
    assert track_key("Song", "A feat. B") == plain
    assert track_key("Song", "A ft B") == plain
    assert track_key("Song (feat. B) [Live]", "A")[0] == "song [live]"


def test_song_literally_named_feat_is_untouched():
    from tidaler.matching import norm_artist, track_key

    # The marker with no guest after it (or no artist before it) is a name,
    # not a credit.
    assert track_key("Song (Feat)", "A") != track_key("Song", "A")
    assert norm_artist("Featuring You") == "featuring you"
    assert norm_artist("Feat") == "feat"


def test_feat_guests_reads_both_fields():
    from tidaler.matching import feat_guests

    assert feat_guests("Song (feat. B)", "A") == frozenset({"b"})
    assert feat_guests("Song", "A feat. B & C") == frozenset({"b", "c"})
    assert feat_guests("Song (feat. B, C and D)", "A") == frozenset({"b", "c", "d"})
    assert feat_guests("Song", "A") == frozenset()


def test_feat_guest_mismatch_refuses_candidate():
    from tidaler.matching import decide_track_presence

    # feat. C on disk, feat. B on screen: different recordings sharing a name.
    idx = _tindex(("Song (feat. C)", "A", {"guests": ["c"]}))
    assert decide_track_presence("Song (feat. B)", "A", idx)["present"] is False


def test_one_sided_feat_credit_matches():
    from tidaler.matching import decide_track_presence

    # The local tagger dropped the credit: still one recording.
    idx = _tindex(("Song", "A", {}))
    assert decide_track_presence("Song (feat. B)", "A", idx)["present"] is True
    # The local tagger wrote it into the artist: still one recording.
    idx2 = _tindex(("Song", "A feat. B", {"guests": ["b"]}))
    assert decide_track_presence("Song (feat. B)", "A", idx2)["present"] is True
    assert decide_track_presence("Song", "A", idx2)["present"] is True


def test_overlapping_guest_lists_agree():
    from tidaler.matching import decide_track_presence

    idx = _tindex(("Song (feat. C)", "A", {"guests": ["c"]}))
    assert decide_track_presence("Song (feat. B, C)", "A", idx)["present"] is True


# ---- disc vocabulary: spelled and Roman numbers, nested vol/part ------------
def test_spelled_and_roman_disc_numbers_group():
    from tidaler.matching import _disc_number

    assert _disc_number("/m/A/Album/Disc Two") == 2
    assert _disc_number("/m/A/Album/CD II") == 2
    assert _disc_number("/m/A/Album/cd.three") == 3
    assert _disc_number("/m/A/Album (Disc Two)") == 2
    idx = _index(
        ("Album", "A", "2005", 9, "/m/A/Album/CD One"),
        ("Album", "A", "2005", 9, "/m/A/Album/Disc Two"),
    )
    r = _decide_presence("Album", "A", "2005", 18, idx)
    assert r["local_tracks"] == 18 and r["partial"] is False


def test_glued_word_markers_are_not_discs():
    from tidaler.matching import _disc_number, disc_group

    # The word forms demand a separator: "Disci" must not read as disc 1.
    assert _disc_number("/m/A/Album/Disci") is None
    assert disc_group("/m/A/Album/Discone") is None


def test_vol_folders_never_group_by_name_alone():
    # "Vol. 2" is a real ALBUM name, and by name alone a nested disc layout is
    # indistinguishable from two series volumes under an artist folder, so the
    # name grouper refuses both and only the files' own disc tags may join a
    # genuine set (below). The artist-level case is the one that mattered: two
    # series-tagged sibling volumes summed into a false complete copy.
    nested = _index(
        ("Album", "A", "2005", 9, "/m/A/Album/Vol. 1"),
        ("Album", "A", "2005", 9, "/m/A/Album/Vol. 2"),
    )
    assert _decide_presence("Album", "A", "2005", 18, nested)["local_tracks"] == 9
    artist_level = _index(
        ("Greatest Hits", "A", "2005", 9, "/m/A/Vol. 1"),
        ("Greatest Hits", "A", "2005", 9, "/m/A/Vol. 2"),
    )
    r = _decide_presence("Greatest Hits", "A", "2005", 18, artist_level)
    assert r["local_tracks"] == 9 and r["full"] is False


def test_vol_disc_layout_still_joins_through_its_tags():
    # The genuine "Album/Vol N" disc layout keeps working: the files' own
    # disc tags are the evidence, which a folder name can never be.
    idx = _shaped(
        ("Album", "A", "2005", 9, "/m/A/Album/Vol. 1", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "2005", 9, "/m/A/Album/Vol. 2", {"disc_no": 2, "disc_total": 2}),
    )
    r = _decide_presence("Album", "A", "2005", 18, idx)
    assert r["local_tracks"] == 18 and r["partial"] is False


def test_vol_suffix_siblings_never_group():
    # "Greatest Hits Vol. 1" beside "Vol. 2": separate releases sharing a
    # stripped stem, exactly what the sibling form must never sum, even when
    # both folders are sloppily tagged with the bare series name.
    idx = _index(
        ("Greatest Hits", "A", "2005", 9, "/m/A/Greatest Hits Vol. 1"),
        ("Greatest Hits", "A", "2005", 9, "/m/A/Greatest Hits Vol. 2"),
    )
    r = _decide_presence("Greatest Hits", "A", "2005", 18, idx)
    assert r["local_tracks"] == 9


def test_part_suffix_albums_stay_separate():
    idx = _index(
        ("Saga", "A", "2005", 9, "/m/A/Saga Part 1"),
        ("Saga", "A", "2005", 9, "/m/A/Saga Part 2"),
    )
    assert _decide_presence("Saga", "A", "2005", 18, idx)["local_tracks"] == 9


def test_duplicate_spelled_disc_numbers_refused():
    # "Disc Two" beside "CD II" name the SAME disc in two styles: two rips,
    # not two discs, and summing them would claim a complete copy.
    idx = _index(
        ("Album", "A", "2005", 9, "/m/A/Album/Disc Two"),
        ("Album", "A", "2005", 9, "/m/A/Album/CD II"),
    )
    assert _decide_presence("Album", "A", "2005", 18, idx)["local_tracks"] == 9


# ---- disc joining: year drift and the shape witness -------------------------
def test_disc_years_off_by_one_still_join():
    idx = _index(
        ("Album", "A", "2005", 10, "/m/A/Album (Disc 1)"),
        ("Album", "A", "2006", 10, "/m/A/Album (Disc 2)"),
    )
    r = _decide_presence("Album", "A", "2005", 20, idx)
    assert r["local_tracks"] == 20 and r["partial"] is False


def test_undated_disc_joins_only_with_matching_disc_total():
    idx = _shaped(
        ("Album", "A", "2005", 10, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "", 10, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 2}),
    )
    r = _decide_presence("Album", "A", "2005", 20, idx)
    assert r["local_tracks"] == 20 and r["partial"] is False


def test_undated_disc_without_shape_witness_still_refused():
    # The original failure stays pinned: an undated disc with nothing but its
    # folder name to vouch for it may be a different pressing entirely.
    idx = _index(
        ("Album", "A", "2005", 10, "/m/A/Album (Disc 1)"),
        ("Album", "A", "", 10, "/m/A/Album (Disc 2)"),
    )
    r = _decide_presence("Album", "A", "2005", 20, idx)
    assert r["local_tracks"] == 10 and r["partial"] is True


def test_undated_disc_with_disagreeing_shape_refused():
    idx = _shaped(
        ("Album", "A", "2005", 10, "/m/A/Album (Disc 1)", {"disc_no": 1, "disc_total": 2}),
        ("Album", "A", "", 10, "/m/A/Album (Disc 2)", {"disc_no": 2, "disc_total": 3}),
    )
    # disc_total disagreeing is no witness at all (and the set would be short
    # of three discs anyway).
    assert _decide_presence("Album", "A", "2005", 20, idx)["local_tracks"] == 10


# ---- audit pins: the gaps the recall-pass audit found -----------------------
def test_anniversary_ordinal_variants_gate_neither_direction():
    # The reverse of the bucket test above: a local ordinal against a plain
    # screen anniversary is present but never sure either.
    idx = _index(("Album (20th Anniversary Edition)", "A", "2020", 12, "fp"))
    r = _decide_presence("Album (Anniversary Edition)", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is False


def test_dash_edition_tail_with_ampersand_parses_after_fold():
    assert _same_edition("Album - Deluxe & Bonus", "Album (Deluxe and Bonus)")
    assert _presence_key("Album - Deluxe & Bonus", "A") == _presence_key("Album (Bonus Deluxe)", "A")


def test_album_artist_feat_credit_folds_at_presence_level():
    assert _presence_key("Album", "A feat. B") == _presence_key("Album", "A")
    idx = _index(("Album", "A feat. B", "2020", 12, "fp"))
    r = _decide_presence("Album", "A", "2020", 12, idx)
    assert r["present"] is True and r["sure"] is True


def test_sibling_word_disc_folders_join_end_to_end():
    idx = _index(
        ("Album", "A", "2005", 9, "/m/A/Album (Disc Two)"),
        ("Album", "A", "2005", 9, "/m/A/Album (Disc Three)"),
    )
    r = _decide_presence("Album", "A", "2005", 18, idx)
    assert r["local_tracks"] == 18 and r["partial"] is False


def test_various_artists_folders_never_enter_the_index():
    # The raw-tag refusal at index build: "V / A" splits at the spaced slash
    # to an artist key of "v", past both VA detectors, so the bridge refuses
    # the row before any key is cut (pinned in test_library_bridge).
    from tidaler.matching import is_various_artists, norm_artist

    assert is_various_artists("V / A")
    assert norm_artist("V / A") == "v"  # why the raw-tag check must come first
