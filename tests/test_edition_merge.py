"""Unit tests for the 'best of both worlds' edition merge.

Pure-function tests: no network, no Qt. The merge takes each shared recording
from the highest-quality edition that has it and the exclusive tracks from the
most complete edition, presenting them all under the complete edition's identity.
"""

import threading

import pytest

from tidaler.waves_ui.backend import (
    WavesBridge,
    _align_edition,
    _as_member_of,
    _build_merge_plan,
    _MergeRec,
    _track_isrc,
)


class _Track:
    def __init__(self, tid, title, dur, isrc=None, track_num=1, volume_num=1):
        self.id = tid
        self.name = title
        self.duration = dur
        self.isrc = isrc
        self.track_num = track_num
        self.volume_num = volume_num
        self.album = None


class _Album:
    """Minimal edition stand-in: a fixed list of _MergeRecs and an audio rank."""

    def __init__(self, aid, tracks, rank):
        self.id = aid
        self.rank = rank
        self.recs = [_MergeRec(t, t.name.lower(), t.duration, _track_isrc(t)) for t in tracks]


def _recs_of(album):
    return album.recs


def _rank_of(album):
    return album.rank


# ---- _track_isrc normalisation ---------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("usrc17607839", "USRC17607839"),
        ("  gbayE0601498  ", "GBAYE0601498"),
        ("", None),
        ("   ", None),
        (None, None),
        (12345, None),
    ],
)
def test_track_isrc(raw, expected):
    assert _track_isrc(_Track("x", "t", 100, isrc=raw)) == expected


# ---- _align_edition ---------------------------------------------------------
def test_align_prefers_isrc_over_title():
    # Same recording, DIFFERENT titles but identical ISRC -> still matched.
    template = [_MergeRec(None, "song one (remaster)", 200, "AAA11111111")]
    other = [_MergeRec(None, "song one", 201, "AAA11111111")]
    assert _align_edition(template, other) == {0: other[0]}


def test_align_title_duration_fallback_without_isrc():
    template = [_MergeRec(None, "intro", 60, None), _MergeRec(None, "outro", 120, None)]
    other = [_MergeRec(None, "outro", 121, None), _MergeRec(None, "intro", 59, None)]
    aligned = _align_edition(template, other)
    assert aligned[0] is other[1] and aligned[1] is other[0]


def test_align_duration_mismatch_blocks_title_match():
    # Same title, far-apart durations -> a distinct recording, not aligned.
    template = [_MergeRec(None, "interlude", 30, None)]
    other = [_MergeRec(None, "interlude", 240, None)]
    assert _align_edition(template, other) == {}


def test_align_isrc_mismatch_vetoes_title_match():
    # Same title + identical duration, but ISRCs prove DIFFERENT recordings
    # (the real A7X "Requiem" case) -> must NOT align.
    template = [_MergeRec(None, "requiem", 261, "USWB11302493")]
    other = [_MergeRec(None, "requiem", 261, "USWB11303180")]
    assert _align_edition(template, other) == {}


def test_align_requires_a_real_duration_on_both_sides():
    # A missing duration is unconfirmable -> never match (caller's guard then bails).
    assert _align_edition([_MergeRec(None, "song", None, None)], [_MergeRec(None, "song", 200, None)]) == {}
    assert _align_edition([_MergeRec(None, "song", 200, None)], [_MergeRec(None, "song", None, None)]) == {}


def test_align_duration_tolerance_is_one_second():
    assert _align_edition([_MergeRec(None, "s", 200, None)], [_MergeRec(None, "s", 201, None)]) != {}
    assert _align_edition([_MergeRec(None, "s", 200, None)], [_MergeRec(None, "s", 202, None)]) == {}


def test_align_never_matches_explicit_to_clean():
    # Same title/length but one explicit, one clean -> different recording, never match,
    # even if ISRCs coincide.
    assert _align_edition([_MergeRec(None, "song", 200, None, True)], [_MergeRec(None, "song", 200, None, False)]) == {}
    assert _align_edition([_MergeRec(None, "song", 200, "X", True)], [_MergeRec(None, "song", 200, "X", False)]) == {}


# ---- _build_merge_plan ------------------------------------------------------
def test_merge_pulls_shared_from_higher_quality_keeps_exclusives():
    # standard (HI-RES, rank 4) is a subset of deluxe (LOSSLESS, rank 2).
    s_a = _Track("s-a", "A", 200, isrc="ISRC0000000A", track_num=1)
    s_b = _Track("s-b", "B", 210, isrc="ISRC0000000B", track_num=2)
    standard = _Album("std", [s_a, s_b], rank=4)
    d_a = _Track("d-a", "A", 200, isrc="ISRC0000000A", track_num=1)
    d_b = _Track("d-b", "B", 210, isrc="ISRC0000000B", track_num=2)
    d_c = _Track("d-c", "C (bonus)", 180, isrc="ISRC0000000C", track_num=3)
    deluxe = _Album("dlx", [d_a, d_b, d_c], rank=2)

    identity, plan = _build_merge_plan([standard, deluxe], _recs_of, _rank_of)

    assert identity is deluxe  # complete edition supplies the identity/structure
    assert [src.id for src, _n, _v in plan] == ["s-a", "s-b", "d-c"]
    assert [n for _s, n, _v in plan] == [1, 2, 3]  # deluxe's numbering preserved


def test_merge_three_editions_picks_best_source_per_track():
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    expanded = _Album("exp", [_Track("e-a", "A", 200), _Track("e-b", "B", 200), _Track("e-c", "C", 200)], rank=3)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200), _Track("d-d", "D", 200)],
        rank=2,
    )
    identity, plan = _build_merge_plan([standard, expanded, deluxe], _recs_of, _rank_of)
    assert identity is deluxe
    # A,B from standard (4); C from expanded (3) not deluxe (2); D only on deluxe.
    assert [src.id for src, _n, _v in plan] == ["s-a", "s-b", "e-c", "d-d"]


def test_no_upgrade_returns_none():
    # The complete edition is ALSO the highest quality -> nothing to merge.
    deluxe = _Album("dlx", [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)], rank=4)
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=3)
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of) == (None, None)


def test_empty_template_returns_none():
    a = _Album("a", [], rank=4)
    b = _Album("b", [], rank=2)
    assert _build_merge_plan([a, b], _recs_of, _rank_of) == (None, None)


def test_no_merge_when_template_not_a_superset():
    # SAFETY: the deluxe (template, most tracks) does NOT contain 'E', which only
    # the tour edition has. A template-based merge would silently drop 'E', so the
    # planner must refuse and let the caller keep the editions intact instead.
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200), _Track("d-d", "D", 200)],
        rank=2,
    )
    tour = _Album(
        "tour",
        [_Track("t-a", "A", 200), _Track("t-b", "B", 200), _Track("t-e", "E (tour-only)", 200)],
        rank=4,
    )
    assert _build_merge_plan([standard, deluxe, tour], _recs_of, _rank_of) == (None, None)
    # Sanity: without the odd tour edition, the same standard+deluxe DOES merge.
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of)[1] is not None


# ---- _as_member_of: re-tag a COPY, never the cached original ----------------
def test_as_member_of_overrides_on_a_copy():
    original_album = object()
    identity_album = object()
    track = _Track("t-1", "Song", 200, track_num=5, volume_num=1)
    track.album = original_album

    member = _as_member_of(track, identity_album, 12, 2)

    assert member is not track
    assert member.id == "t-1"  # same recording -> same stream
    assert member.album is identity_album
    assert member.track_num == 12
    assert member.volume_num == 2
    # The cached original is untouched.
    assert track.album is original_album
    assert track.track_num == 5
    assert track.volume_num == 1


# ---- _download_merge_plan fans out one dl.item() per plan track -------------
class _FakeSettingsData:
    downloads_concurrent_max = 2


class _FakeSettings:
    data = _FakeSettingsData()


class _RecordingDownload:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def item(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
        return True, "/tmp/out"


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, v):
        self.values.append(v)


class _FakeSignals:
    def __init__(self):
        self.list_item = _Signal()


def test_download_merge_plan_calls_item_per_track():
    bridge = WavesBridge.__new__(WavesBridge)  # no Qt/network init
    bridge.settings = _FakeSettings()

    identity = object()
    plan = [
        (_Track("s-a", "A", 200), 1, 1),
        (_Track("s-b", "B", 210), 2, 1),
        (_Track("d-c", "C", 180), 3, 1),
    ]
    dl = _RecordingDownload()
    signals = _FakeSignals()
    job_abort = threading.Event()

    bridge._download_merge_plan(dl, signals, job_abort, identity, "tmpl", plan)

    assert len(dl.calls) == 3
    for call in dl.calls:
        assert call["is_parent_album"] is True
        assert call["list_total"] == 3
        assert call["media"].album is identity  # re-tagged onto the identity album
        assert call["keep_album"] is True  # so item() won't re-fetch and clobber the identity
        assert call["event_stop"] is job_abort
    assert sorted(c["list_position"] for c in dl.calls) == [1, 2, 3]
    # Each source recording is preserved (the stream still comes from its edition).
    assert {c["media"].id for c in dl.calls} == {"s-a", "s-b", "d-c"}
    # List progress reaches 100% once every track is done.
    assert signals.list_item.values and signals.list_item.values[-1] == pytest.approx(100.0)


def test_download_merge_plan_raises_when_a_track_fails():
    # A partially-failed merge must NOT be reported as a clean success.
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    plan = [(_Track("a", "A", 100), 1, 1), (_Track("b", "B", 100), 2, 1)]

    class _PartialDownload:
        def item(self, **kw):
            return kw["list_position"] == 1, "/tmp/x"  # second track "fails" (ok=False)

    with pytest.raises(RuntimeError):
        bridge._download_merge_plan(_PartialDownload(), _FakeSignals(), threading.Event(), object(), "tmpl", plan)


# ---- _album_key keeps same-titled editions with different track counts apart -
class _KeyArtist:
    def __init__(self, name):
        self.name = name


class _KeyAlbum:
    def __init__(self, name, artist_name, num_tracks):
        self.name = name
        self.full_name = name
        self.artist = _KeyArtist(artist_name)
        self.artists = None
        self.num_tracks = num_tracks


def test_album_key_separates_same_title_different_track_counts():
    # The pre-merge quality dedup must not collapse two same-titled editions that
    # differ in track count (it keeps only the best quality, dropping the other's
    # unique songs). Track count is part of the key, so they survive to the
    # track-aware edition stage.
    bridge = WavesBridge.__new__(WavesBridge)
    short = _KeyAlbum("Greatest Hits", "Band", 18)
    long = _KeyAlbum("Greatest Hits", "Band", 22)
    dupe = _KeyAlbum("Greatest Hits", "Band", 18)  # same release at another quality
    assert bridge._album_key(short) != bridge._album_key(long)  # different content -> kept apart
    assert bridge._album_key(short) == bridge._album_key(dupe)  # true duplicate -> still collapses
