"""Unit tests for the 'best of both worlds' edition merge.

Pure-function tests: no network, no Qt. The merge takes each shared recording
from the highest-quality edition that has it and the exclusive tracks from the
most complete edition, presenting them all under the complete edition's identity.
"""

import threading
from types import SimpleNamespace

import pytest
from tidalapi.media import Quality

from waves.download import Download
from waves.waves_ui.backend import (
    WavesBridge,
    _align_edition,
    _as_member_of,
    _build_merge_plan,
    _merge_rec_title,
    _MergeRec,
    _PlanEntry,
    _track_isrc,
    _TrackedDownload,
)


class _Track:
    def __init__(self, tid, title, dur, isrc=None, track_num=1, volume_num=1, rank=None, explicit=False):
        self.id = tid
        self.name = title
        self.duration = dur
        self.isrc = isrc
        self.track_num = track_num
        self.volume_num = volume_num
        self.explicit = explicit
        # None means "inherit the edition's tier", which is what an album-wide
        # rank meant before ranking became per-recording. Set it explicitly to
        # model an edition whose tracks are NOT all at its advertised ceiling.
        self.rank = rank
        self.album = None
        # A real tidalapi Track always carries this; the engine's destination
        # decision reads it to guess the extension.
        self.media_metadata_tags: list = []


# The tidalapi tier a stand-in's numeric rank advertises, so the PRODUCTION rank
# reader (_quality_rank, via WavesBridge._merge_rank_fn) sees the same tier the
# test's shortcut _rank_of does. Empty media_metadata_tags make it fall through
# to audio_quality, exactly like a not-yet-available track does.
_QUALITY_OF = {4: Quality.hi_res_lossless, 3: Quality.high_lossless, 2: Quality.low_320k, 1: Quality.low_96k}


class _Album:
    """Minimal edition stand-in: a fixed list of _MergeRecs and an audio rank."""

    def __init__(self, aid, tracks, rank):
        self.id = aid
        self.rank = rank
        self.audio_quality = _QUALITY_OF.get(rank)
        self.media_metadata_tags: list = []
        for t in tracks:
            if t.rank is None:
                t.rank = rank
            t.audio_quality = _QUALITY_OF.get(t.rank)
        # Titles go through the production normaliser, not a shortcut, so a
        # degenerate title behaves here exactly as it does in recs_of.
        self.recs = [_MergeRec(t, _merge_rec_title(t), t.duration, _track_isrc(t), t.explicit) for t in tracks]


def _recs_of(album):
    return album.recs


def _rank_of(obj):
    """Serves albums (the template tie-break) and tracks (the per-slot choice)."""
    return getattr(obj, "rank", 0)


def _bridge_capped_at(quality):
    """A WavesBridge whose settings pin the audio quality at ``quality``, built
    without Qt or network so the real _merge_rank_fn can be exercised."""
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality))
    return bridge


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

    identity, plan, reason = _build_merge_plan([standard, deluxe], _recs_of, _rank_of)

    assert reason == ""

    assert identity is deluxe  # complete edition supplies the identity/structure
    assert [entry.src.id for entry in plan] == ["s-a", "s-b", "d-c"]
    assert [entry.track_num for entry in plan] == [1, 2, 3]  # deluxe's numbering preserved
    # Every slot carries the IDENTITY edition's track id: that is the id the
    # download is recorded and browsed under, even when the audio comes from
    # another edition.
    assert [entry.identity_id for entry in plan] == ["d-a", "d-b", "d-c"]


def test_merge_three_editions_picks_best_source_per_track():
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    expanded = _Album("exp", [_Track("e-a", "A", 200), _Track("e-b", "B", 200), _Track("e-c", "C", 200)], rank=3)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200), _Track("d-d", "D", 200)],
        rank=2,
    )
    identity, plan, _ = _build_merge_plan([standard, expanded, deluxe], _recs_of, _rank_of)
    assert identity is deluxe
    # A,B from standard (4); C from expanded (3) not deluxe (2); D only on deluxe.
    assert [entry.src.id for entry in plan] == ["s-a", "s-b", "e-c", "d-d"]
    assert [entry.identity_id for entry in plan] == ["d-a", "d-b", "d-c", "d-d"]


def test_no_upgrade_returns_none():
    # The complete edition is ALSO the highest quality -> nothing to merge.
    deluxe = _Album("dlx", [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)], rank=4)
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=3)
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of) == (None, None, "no_upgrade")


def test_empty_template_returns_none():
    a = _Album("a", [], rank=4)
    b = _Album("b", [], rank=2)
    assert _build_merge_plan([a, b], _recs_of, _rank_of) == (None, None, "no_template_tracks")


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
    assert _build_merge_plan([standard, deluxe, tour], _recs_of, _rank_of) == (None, None, "not_superset")
    # Sanity: without the odd tour edition, the same standard+deluxe DOES merge.
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of)[1] is not None


def test_no_merge_when_an_editions_tracks_are_unknown():
    # SAFETY: recs_of yields [] both when the track fetch FAILED and for a
    # region-locked edition. Empty means unknown content, not no content: the
    # superset guard would read 0 aligned < 0 recs and pass vacuously, and the
    # tour edition's exclusive track would silently vanish from the download.
    # The planner must refuse so the caller keeps the editions intact.
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)],
        rank=2,
    )
    tour = _Album("tour", [], rank=3)  # fetch failed or region-locked: no recs

    assert _build_merge_plan([standard, deluxe, tour], _recs_of, _rank_of) == (None, None, "unknown_edition")
    # Sanity: without the unknown edition the same group DOES merge.
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of)[1] is not None


# ---- _as_member_of: re-tag a COPY, never the cached original ----------------
def test_as_member_of_overrides_on_a_copy():
    original_album = object()
    identity_album = object()
    track = _Track("t-1", "Song", 200, track_num=5, volume_num=1)
    track.album = original_album

    member = _as_member_of(track, identity_album, 12, 2, "identity-9")

    assert member is not track
    assert member.id == "t-1"  # same recording -> same stream
    assert member.waves_identity_id == "identity-9"  # but recorded under the identity slot
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
    download_delay = False


class _FakeSettings:
    data = _FakeSettingsData()


class _EngineShaped:
    """What every dl stand-in here owes the merge fan-out: the refusal
    counter it reads, and the two engine methods it calls around the pool,
    exactly as items() does. The real path collector is borrowed (pure, no
    I/O); the playlist step is recorded rather than written, since these
    tests are about the fan-out, not the file."""

    unavailable_count = 0
    _landed_paths = staticmethod(Download._landed_paths)

    def __init__(self):
        self.playlist_calls = []

    def _playlist_for_collection(self, media, file_template, result_paths):
        self.playlist_calls.append((media, file_template, list(result_paths)))


class _RecordingDownload(_EngineShaped):
    def __init__(self):
        super().__init__()
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
        _PlanEntry(_Track("s-a", "A", 200), 1, 1, "d-a"),
        _PlanEntry(_Track("s-b", "B", 210), 2, 1, "d-b"),
        _PlanEntry(_Track("d-c", "C", 180), 3, 1, "d-c"),
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
    # Each source recording is preserved (the stream still comes from its edition)
    # while every member carries its identity slot for record-keeping.
    assert {c["media"].id for c in dl.calls} == {"s-a", "s-b", "d-c"}
    assert {c["media"].waves_identity_id for c in dl.calls} == {"d-a", "d-b", "d-c"}
    # List progress reaches 100% once every track is done.
    assert signals.list_item.values and signals.list_item.values[-1] == pytest.approx(100.0)
    # And the step items() ends with: the album's playlist, under the identity
    # album's name, from the landed paths, once.
    assert [(m, t) for m, t, _p in dl.playlist_calls] == [(identity, "tmpl")]


def test_download_merge_plan_raises_when_a_track_fails():
    # A partially-failed merge must NOT be reported as a clean success.
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    plan = [_PlanEntry(_Track("a", "A", 100), 1, 1, "a"), _PlanEntry(_Track("b", "B", 100), 2, 1, "b")]

    class _PartialDownload(_EngineShaped):
        def item(self, **kw):
            return kw["list_position"] == 1, "/tmp/x"  # second track "fails" (ok=False)

    with pytest.raises(RuntimeError):
        bridge._download_merge_plan(_PartialDownload(), _FakeSignals(), threading.Event(), object(), "tmpl", plan)


# ---- queue drawer registry: rows keyed by identity ids ----------------------
def test_seed_merge_registry_keys_rows_by_identity_id():
    # loadQueueTracks fetches the IDENTITY album's track list and joins it to
    # this registry by id; source-id keys would miss, freezing rows at pending
    # and appending ghost rows (a 3-track album rendered as 5 rows).
    from waves.waves_ui.backend import _seed_merge_registry

    plan = [
        _PlanEntry(_Track("s-a", "A", 200), 1, 1, "d-a"),
        _PlanEntry(_Track("d-c", "C", 180), 2, 1, "d-c"),
    ]
    reg = _seed_merge_registry(plan)
    assert sorted(reg) == ["d-a", "d-c"]
    assert all(reg[k]["id"] == k and reg[k]["status"] == "pending" for k in reg)
    assert _seed_merge_registry(None) == {}


# ---- a refused track is not a failed track ----------------------------------
class _RefusingDownload(_EngineShaped):
    """Second track is one TIDAL withholds: item() returns ok=False AND bumps
    unavailable_count, exactly as _TrackedDownload.item does for a refusal."""

    def __init__(self):
        super().__init__()
        self.unavailable_count = 0
        self._lock = threading.Lock()

    def item(self, **kw):
        if kw["list_position"] == 2:
            with self._lock:
                self.unavailable_count += 1
            return False, ""
        return True, "/tmp/out"


def test_a_withheld_track_does_not_fail_the_merge():
    # A delisted song is not a failure: the app did all it could and the rest of
    # the album is on disk. Counting refusals turned one withheld track into a
    # red album, and since the plan is only dropped on success every retry
    # replayed it and failed the same way (issue #25, in the merge path).
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    plan = [
        _PlanEntry(_Track("a", "A", 100), 1, 1, "a"),
        _PlanEntry(_Track("b", "B", 100), 2, 1, "b"),
        _PlanEntry(_Track("c", "C", 100), 3, 1, "c"),
    ]

    bridge._download_merge_plan(_RefusingDownload(), _FakeSignals(), threading.Event(), object(), "tmpl", plan)


def test_an_all_refused_merge_does_not_report_a_clean_success():
    # The other side of the refusal rule: a refusal writes nothing, so an album
    # TIDAL withheld entirely must say so rather than report done over an empty
    # folder. Same line _collection_incomplete_reason draws for a plain album.
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    plan = [_PlanEntry(_Track("a", "A", 100), 1, 1, "a"), _PlanEntry(_Track("b", "B", 100), 2, 1, "b")]

    class _AllRefused(_EngineShaped):
        def __init__(self):
            super().__init__()
            self.unavailable_count = 0
            self._lock = threading.Lock()

        def item(self, **kw):
            with self._lock:
                self.unavailable_count += 1
            return False, ""

    with pytest.raises(RuntimeError):
        bridge._download_merge_plan(_AllRefused(), _FakeSignals(), threading.Event(), object(), "tmpl", plan)


def test_a_hard_failure_still_raises():
    # The other half of the contract: a genuine failure (no refusal recorded)
    # must still fail the job so it can be retried.
    bridge = WavesBridge.__new__(WavesBridge)
    bridge.settings = _FakeSettings()
    plan = [_PlanEntry(_Track("a", "A", 100), 1, 1, "a"), _PlanEntry(_Track("b", "B", 100), 2, 1, "b")]

    class _PartialDownload(_EngineShaped):
        unavailable_count = 0

        def item(self, **kw):
            return kw["list_position"] == 1, "/tmp/x"

    with pytest.raises(RuntimeError):
        bridge._download_merge_plan(_PartialDownload(), _FakeSignals(), threading.Event(), object(), "tmpl", plan)


# ---- quality is judged per recording, and only up to the user's cap ---------
def test_rank_is_read_per_recording_not_per_edition():
    # The standard edition ADVERTISES rank 4 because of one hi-res bonus mix, but
    # the two songs it shares with the deluxe are plain rank 3, same as the
    # template's. Ranking the release borrowed both for no gain at all.
    standard = _Album("std", [_Track("s-a", "A", 200, rank=3), _Track("s-b", "B", 200, rank=3)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)],
        rank=3,
    )
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of) == (None, None, "no_upgrade")


def test_template_baseline_is_its_own_recording_not_its_advertised_tier():
    # The mirror image: the TEMPLATE advertises a low ceiling (rank 2, a lossy
    # deluxe) but its own recording of A is a rank 4 master. The standard's A is
    # a plain rank 3. Judging the template's side by the album (2) would call
    # that 3 an upgrade and swap a worse file over the template's best one; the
    # bar for a borrow is the recording already in the slot (4), so nothing moves.
    standard = _Album("std", [_Track("s-a", "A", 200, rank=3), _Track("s-b", "B", 200, rank=2)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200, rank=4), _Track("d-b", "B", 200), _Track("d-c", "C", 200)],
        rank=2,
    )
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of) == (None, None, "no_upgrade")


def test_a_lower_tier_recording_is_never_borrowed():
    # The other edition advertises rank 4, but THIS recording is rank 2 while the
    # template's is 3. Ranking the release swapped in a worse file than a plain
    # download would have produced.
    standard = _Album("std", [_Track("s-a", "A", 200, rank=2), _Track("s-b", "B", 200, rank=4)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)],
        rank=3,
    )
    identity, plan, reason = _build_merge_plan([standard, deluxe], _recs_of, _rank_of)

    assert reason == "" and identity is deluxe
    # Slot A keeps the template's own (rank 3) copy; only B is a real upgrade.
    assert [entry.src.id for entry in plan] == ["d-a", "s-b", "d-c"]


def test_an_upgrade_above_the_user_cap_does_not_merge():
    # Capped at LOSSLESS (3), a HI-RES edition delivers exactly what the deluxe
    # does, so assembling one album from two editions buys nothing. The clamp
    # under test is the bridge's own _merge_rank_fn (settings cap applied to the
    # production _quality_rank), not a stand-in built here.
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)],
        rank=3,
    )
    capped = _bridge_capped_at(Quality.high_lossless)._merge_rank_fn()
    assert _build_merge_plan([standard, deluxe], _recs_of, capped) == (None, None, "no_upgrade")
    # Sanity: with the cap raised to HI-RES, the very same group, through the
    # very same production rank function, DOES merge (both shared songs borrowed).
    uncapped = _bridge_capped_at(Quality.hi_res_lossless)._merge_rank_fn()
    identity, plan, reason = _build_merge_plan([standard, deluxe], _recs_of, uncapped)
    assert reason == "" and identity is deluxe
    assert [entry.src.id for entry in plan] == ["s-a", "s-b", "d-c"]
    # And the production reader really does SEE these stand-ins: if _quality_rank
    # could not read the tier it would answer a constant for every recording, and
    # the capped decline above would be true for the wrong reason. Pin the one
    # recording the whole decision turns on, on both sides of the clamp.
    assert (capped(standard.recs[0].obj), uncapped(standard.recs[0].obj)) == (3, 4)


# ---- a track whose title normalises away is still a track -------------------
@pytest.mark.parametrize("raw", [None, "", "   ", "(Live)", "(Mono Version)"])
def test_merge_rec_title_is_never_empty(raw):
    # An empty key would be dropped by the old filter, or (worse) compare equal
    # to another empty one and get paired by the duration window.
    assert _merge_rec_title(_Track("t", raw, 200))


def test_two_nameless_tracks_never_collide():
    assert _merge_rec_title(_Track("t-1", None, 200)) != _merge_rec_title(_Track("t-2", None, 200))


def test_a_track_with_an_unmatchable_title_still_takes_a_plan_slot():
    # The template's third track has no usable name. It must still get a slot, or
    # the merged album ships a song short while reporting a clean 100%.
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-b", "B", 200)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-x", None, 200)],
        rank=2,
    )
    identity, plan, reason = _build_merge_plan([standard, deluxe], _recs_of, _rank_of)

    assert reason == "" and identity is deluxe
    assert len(plan) == 3
    assert "d-x" in [entry.identity_id for entry in plan]


def test_an_unnameable_track_on_another_edition_blocks_the_merge():
    # SAFETY, mirroring test_no_merge_when_an_editions_tracks_are_unknown: a
    # nameless track on a NON-template edition cannot be proven to exist on the
    # template, so the merge must refuse rather than drop it.
    standard = _Album("std", [_Track("s-a", "A", 200), _Track("s-x", None, 200)], rank=4)
    deluxe = _Album(
        "dlx",
        [_Track("d-a", "A", 200), _Track("d-b", "B", 200), _Track("d-c", "C", 200)],
        rank=2,
    )
    assert _build_merge_plan([standard, deluxe], _recs_of, _rank_of) == (None, None, "not_superset")


# ---- the identity must not depend on input order ----------------------------
def test_template_tie_break_is_order_independent():
    # Two equal-count, equal-tier editions ("(Deluxe)" and "(Deluxe Edition)")
    # tie on everything the template pick weighs, yet each holds the better
    # master of a different song, so a merge exists whichever is the identity.
    # The clicked-album path and the discography path feed different orders, so
    # an unstable pick wrote the same songs into two folders. The tie is settled
    # by the album id string (max, so the higher one wins), never by position.
    a = _Album("aaa", [_Track("a-1", "A", 200, rank=4), _Track("a-2", "B", 200, rank=3)], rank=3)
    b = _Album("bbb", [_Track("b-1", "A", 200, rank=3), _Track("b-2", "B", 200, rank=4)], rank=3)

    identity_ab, plan_ab, reason_ab = _build_merge_plan([a, b], _recs_of, _rank_of)
    identity_ba, plan_ba, reason_ba = _build_merge_plan([b, a], _recs_of, _rank_of)

    assert reason_ab == "" and reason_ba == ""
    assert identity_ab is identity_ba is b
    # And the plan is the same album both ways: b's layout, A borrowed from a.
    assert [entry.src.id for entry in plan_ab] == [entry.src.id for entry in plan_ba] == ["a-1", "b-2"]


def test_template_tie_break_follows_the_id_not_the_order():
    # Same two editions, ids swapped: the album holding the better A is now the
    # higher id, so IT becomes the identity, from either input order. If the
    # pick leaned on position, first- or last-added would win instead.
    a = _Album("bbb", [_Track("a-1", "A", 200, rank=4), _Track("a-2", "B", 200, rank=3)], rank=3)
    b = _Album("aaa", [_Track("b-1", "A", 200, rank=3), _Track("b-2", "B", 200, rank=4)], rank=3)

    identity_ab, plan_ab, _ = _build_merge_plan([a, b], _recs_of, _rank_of)
    identity_ba, plan_ba, _ = _build_merge_plan([b, a], _recs_of, _rank_of)

    assert identity_ab is identity_ba is a
    assert [entry.src.id for entry in plan_ab] == [entry.src.id for entry in plan_ba] == ["a-1", "b-2"]


# ---- one ISRC on two different cuts -----------------------------------------
def test_duplicate_isrc_pairs_by_closest_recording():
    # An album cut and its radio edit sharing one ISRC (an ISO 3901 violation,
    # but real), listed in opposite order across the two editions. Taking the
    # first free candidate swapped the slots, so each song landed under the
    # other's number and title.
    template = [_MergeRec("L", "song", 320, "DUP11111111"), _MergeRec("S", "song (radio edit)", 190, "DUP11111111")]
    other = [_MergeRec("s2", "song (radio edit)", 190, "DUP11111111"), _MergeRec("l2", "song", 320, "DUP11111111")]

    aligned = _align_edition(template, other)

    assert aligned[0] is other[1], "the 5:20 cut must pair with the 5:20 cut"
    assert aligned[1] is other[0]


# ---- the id written into the file is the one the guards ask about -----------
def test_a_merged_member_is_filed_under_the_identity_id():
    # download.py stamps an item id into the file and compares it back to decide
    # whether a destination holds Waves' own copy. Writing the SOURCE edition's
    # id meant a later plain job asked with the identity id, failed to recognise
    # the file, and wrote a _01 duplicate beside it instead of replacing it.
    from waves.download import _waves_item_id, _waves_owned_ids

    plain = _Track("t-1", "Song", 200)
    assert _waves_item_id(plain) == "t-1", "an ordinary track is filed under its own id"
    assert _waves_owned_ids(plain) == {"t-1"}

    member = _as_member_of(plain, object(), 3, 1, "identity-9")
    assert member.id == "t-1", "the stream still comes from the source edition"
    assert _waves_item_id(member) == "identity-9"
    # Builds up to v0.1.21 wrote the SOURCE id into this file, so a library
    # already on disk is tagged the other way. Both count as our own copy, or a
    # forced re-save drops a numbered duplicate the app will never delete.
    assert _waves_owned_ids(member) == {"identity-9", "t-1"}


# ---- ownership gate: merge members skip only at THIS job's destination -------
class _GateSettingsData:
    album_track_num_pad_min = 0
    filename_delimiter_artist = ", "
    filename_delimiter_album_artist = ", "
    filename_illegal_replacement = ""
    use_primary_album_artist = False
    download_dolby_atmos = False


class _GateSettings:
    data = _GateSettingsData()


def _gate_dl(records: dict, tmp_path) -> _TrackedDownload:
    dl = _TrackedDownload.__new__(_TrackedDownload)  # no engine init: gate-only
    dl._ownership_of = lambda mid: records.get(mid)
    dl._target_rank = 2
    dl.settings = _GateSettings()
    dl.path_base = str(tmp_path)
    dl._force_redownload = False
    return dl


def test_merge_member_owned_elsewhere_is_not_skipped(tmp_path):
    # The identity track is owned, but in ANOTHER folder (the standard
    # edition's, or a playlist's). Skipping would leave a hole in the merged
    # album while the job still reports done, so the gate must not fire.
    member = _as_member_of(_Track("src-1", "Song", 200), object(), 1, 1, "id-1")
    records = {"id-1": {"path": str(tmp_path / "Standard" / "01 Song.flac"), "quality_rank": 4}}
    dl = _gate_dl(records, tmp_path)

    assert dl._ownership_verdict(member, "Merged/file") is None


def test_merge_member_owned_at_the_destination_is_skipped(tmp_path):
    member = _as_member_of(_Track("src-1", "Song", 200), object(), 1, 1, "id-1")
    records = {"id-1": {"path": str(tmp_path / "Merged" / "01 Song.flac"), "quality_rank": 4}}
    dl = _gate_dl(records, tmp_path)

    assert dl._ownership_verdict(member, "Merged/file") == "skip"


def test_merge_member_is_looked_up_by_identity_id_not_source_id(tmp_path):
    # The download gets RECORDED under the identity id, so a record under the
    # source edition's id (a leftover from the pre-fix era, or that edition's
    # own download) must not satisfy the gate.
    member = _as_member_of(_Track("src-1", "Song", 200), object(), 1, 1, "id-1")
    records = {"src-1": {"path": str(tmp_path / "Merged" / "01 Song.flac"), "quality_rank": 4}}
    dl = _gate_dl(records, tmp_path)

    assert dl._ownership_verdict(member, "Merged/file") is None


def test_plain_item_gate_is_unchanged_by_the_destination_rule(tmp_path):
    # A non-merge item keeps the deliberate cross-folder dedupe (a playlist
    # track owned via an album download stays skipped, no duplicate file).
    track = _Track("t-1", "Song", 200)
    records = {"t-1": {"path": str(tmp_path / "SomeAlbum" / "01 Song.flac"), "quality_rank": 4}}
    dl = _gate_dl(records, tmp_path)

    assert dl._ownership_verdict(track, "Playlists/file") == "skip"


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
