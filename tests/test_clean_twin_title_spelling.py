"""A clean edition spelled differently from its explicit twin still meets it.

WHAT THIS FENCES OFF (issue #60)
--------------------------------
TIDAL lists 50 Cent's explicit "Get Rich Or Die Tryin'" beside clean editions
titled "Get Rich Or Die Tryin" (no apostrophe), and the explicit single "Too
Rich For The Bitch" beside a clean "Too Rich For The B*tch". Every duplicate
stage groups by normalized title, so the clean twins keyed apart, the explicit
preference never saw a choice, and a discography queued all of them.

THE RULE
--------
* Apostrophes carry no identity in a grouping title (straight, curly, modifier,
  backtick, acute).
* A CLEAN title that masks letters with '*' takes the key of the ONE EXPLICIT
  uncensored title that fits its shape and agrees on the rest of the key. No
  fit, more than one fit, or matching explicit flags leave it alone.
"""

from __future__ import annotations

from types import SimpleNamespace

from tidalapi.album import Album
from tidalapi.media import Quality, Track

from waves.waves_ui import backend


def _album(aid, title, *, explicit, tracks=10, artist="50 Cent", quality=Quality.high_lossless):
    a = Album.__new__(Album)
    a.id = aid
    a.name = title
    a.artist = SimpleNamespace(name=artist, id=1)
    a.artists = [a.artist]
    a.audio_modes = ["STEREO"]
    a.audio_quality = quality
    a.media_metadata_tags = None
    a.num_tracks = tracks
    a.num_videos = 0
    a.explicit = explicit
    return a


def _track(tid, title, *, explicit, duration=200):
    t = Track.__new__(Track)
    t.id = tid
    t.name = title
    t.full_name = title
    t.artist = SimpleNamespace(name="50 Cent", id=1)
    t.artists = [t.artist]
    t.audio_modes = ["STEREO"]
    t.audio_quality = Quality.high_lossless
    t.media_metadata_tags = None
    t.duration = duration
    t.explicit = explicit
    return t


def _bridge(mode="explicit"):
    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=Quality.hi_res_lossless))
    b._waves_prefs = {"explicit_mode": mode}
    for name in ("_album_key", "_track_key", "_max_quality_rank", "_dedup_albums", "_dedup_tracks"):
        setattr(b, name, getattr(backend.WavesBridge, name).__get__(b, backend.WavesBridge))
    return b


def _ids(items):
    return [x.id for x in items]


# --------------------------------------------------------------------------- #
# Apostrophes
# --------------------------------------------------------------------------- #
def test_every_apostrophe_form_normalizes_away():
    want = backend._norm_title("Get Rich Or Die Tryin")
    for mark in ("'", "‘", "’", "ʼ", "`", "´"):
        assert backend._norm_title(f"Get Rich Or Die Tryin{mark}") == want, repr(mark)
    assert backend._norm_title("Rock'n'Roll (Explicit)") == "rocknroll"


def test_the_apostrophe_less_clean_editions_follow_the_explicit_preference():
    exp = _album("e", "Get Rich Or Die Tryin'", explicit=True)
    clean_a = _album("c1", "Get Rich Or Die Tryin", explicit=False)
    clean_b = _album("c2", "Get Rich Or Die Tryin", explicit=False, quality=Quality.low_320k)
    assert _ids(_bridge("explicit")._dedup_albums([clean_a, exp, clean_b])) == ["e"]
    assert _ids(_bridge("clean")._dedup_albums([clean_a, exp, clean_b])) == ["c1"]
    assert sorted(_ids(_bridge("both")._dedup_albums([clean_a, exp, clean_b]))) == ["c1", "e"]


def test_the_edition_collapse_key_ignores_apostrophes_too():
    """With a different track count the twins survive the dedup and meet in
    the edition stage, which groups by the same normalized title."""
    exp = _album("e", "Get Rich Or Die Tryin'", explicit=True, tracks=19)
    cln = _album("c", "Get Rich Or Die Tryin", explicit=False, tracks=18)
    assert backend._edition_base_key(exp) == backend._edition_base_key(cln)


# --------------------------------------------------------------------------- #
# Censored titles
# --------------------------------------------------------------------------- #
def test_a_censored_clean_single_meets_its_explicit_twin():
    exp = _album("e", "Too Rich For The Bitch", explicit=True, tracks=1)
    cln = _album("c", "Too Rich For The B*tch", explicit=False, tracks=1)
    assert _ids(_bridge("explicit")._dedup_albums([cln, exp])) == ["e"]
    assert _ids(_bridge("clean")._dedup_albums([cln, exp])) == ["c"]
    assert sorted(_ids(_bridge("both")._dedup_albums([cln, exp]))) == ["c", "e"]


def test_a_run_of_asterisks_reads_as_several_letters():
    exp = _album("e", "Fuck Love", explicit=True, artist="X")
    cln = _album("c", "F**k Love", explicit=False, artist="X")
    assert _ids(_bridge()._dedup_albums([cln, exp])) == ["e"]


def test_a_censored_track_meets_its_explicit_twin():
    exp = _track("e", "Too Rich For The Bitch", explicit=True, duration=201)
    cln = _track("c", "Too Rich For The B*tch", explicit=False, duration=200)
    assert _ids(_bridge()._dedup_tracks([cln, exp])) == ["e"]


def test_a_censored_track_far_from_its_twin_in_length_is_another_recording():
    # Cluster numbers start at 0 within each title, so aliasing after the
    # length clustering paired the first clean cluster with the first explicit
    # one whatever the lengths: the 420 s clean cut was dropped as a twin.
    exp = _track("e", "Too Rich For The Bitch", explicit=True, duration=200)
    cln = _track("c", "Too Rich For The B*tch", explicit=False, duration=420)
    assert sorted(_ids(_bridge()._dedup_tracks([exp, cln]))) == ["c", "e"]


def test_a_censored_track_pairs_with_the_explicit_cut_of_its_own_length():
    short = _track("e1", "Too Rich For The Bitch", explicit=True, duration=200)
    long_e = _track("e2", "Too Rich For The Bitch", explicit=True, duration=420)
    long_c = _track("c", "Too Rich For The B*tch", explicit=False, duration=421)
    assert sorted(_ids(_bridge("clean")._dedup_tracks([short, long_e, long_c]))) == ["c", "e1"]
    assert sorted(_ids(_bridge()._dedup_tracks([short, long_e, long_c]))) == ["e1", "e2"]


def test_two_possible_twins_are_never_guessed_between():
    shot = _album("1", "Shot", explicit=True)
    shut = _album("2", "Shut", explicit=True)
    masked = _album("3", "Sh*t", explicit=False)
    assert _ids(_bridge()._dedup_albums([shot, shut, masked])) == ["1", "2", "3"]


def test_the_twin_must_agree_on_artist_and_track_count():
    exp = _album("e", "Too Rich For The Bitch", explicit=True, tracks=1)
    other_count = _album("c", "Too Rich For The B*tch", explicit=False, tracks=3)
    other_artist = _album("a", "Too Rich For The B*tch", explicit=False, tracks=1, artist="Someone Else")
    assert _ids(_bridge()._dedup_albums([exp, other_count, other_artist])) == ["e", "c", "a"]


def test_matching_explicit_flags_are_not_a_clean_twin():
    """A stylized title ("*NSYNC") is not a masked word: without the clean /
    explicit signature it is never folded into another release."""
    a = _album("1", "XNSYNC", explicit=False, artist="Band")
    b = _album("2", "*NSYNC", explicit=False, artist="Band")
    assert _ids(_bridge()._dedup_albums([a, b])) == ["1", "2"]
    c = _album("3", "Bitch", explicit=True, artist="Band")
    d = _album("4", "B*tch", explicit=True, artist="Band")
    assert _ids(_bridge()._dedup_albums([c, d])) == ["3", "4"]


def test_asterisks_that_mask_no_letters_are_styling():
    exp = _album("e", "Love", explicit=True, artist="Band")
    starred = _album("s", "***", explicit=False, artist="Band")
    assert _ids(_bridge()._dedup_albums([exp, starred])) == ["e", "s"]
