"""A Dolby Atmos copy is judged on the tier Atmos is actually served at.

WHAT THIS FENCES OFF
--------------------
TIDAL serves Dolby Atmos only through a session pinned to
``ATMOS_REQUEST_QUALITY`` (constants.py), so an Atmos file arrives at that tier
no matter what the audio quality setting says. Waves recorded the echoed tier
and then ranked it on the ordinary stereo scale, where it sits below any
Lossless or Max target. The consequences all followed from that one comparison:

  * ``_ownership_decision`` answered "force" for a track already on disk, which
    turns skip_existing off and re-fetches and overwrites the identical file;
  * the re-fetch recorded the same tier again, so the next download forced
    again, and the next: a loop with no state in which it settles;
  * ``ownershipOf`` answered ``up_to_date: False`` forever, so the track's
    button never left DOWNLOAD and ``_rollup_verdict`` answered "no" for the
    whole album card;
  * the queue drawer predicted a download for a track already on disk.

Reachable with "Download Dolby Atmos" on and audio quality at Lossless or Max,
which is the ordinary setting for the accounts that can get Atmos at all.

The rule now: an Atmos copy satisfies a job that would fetch Atmos. The request
tier is a constant this app cannot raise, so the next fetch would ask for
exactly what the last one already got.

HOW THIS STAYS FIXED
--------------------
The gate is the REAL ``_TrackedDownload._ownership_decision`` reading a REAL
``OwnershipStore`` with real files on disk, and the query is the REAL
``WavesBridge.ownershipOf``. The premise the whole thing rests on (that the
Atmos request tier ranks below a Lossless target) is pinned against the real
constant rather than the string it equals today, and the engine's own Atmos
condition is driven directly so the mirror in backend.py cannot drift from it.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from tidalapi.media import AudioMode, Quality, Track

from tidaler.constants import ATMOS_REQUEST_QUALITY
from tidaler.download import Download
from tidaler.ownership import OwnershipStore, quality_rank
from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import (
    WavesBridge,
    _collection_incomplete_reason,
    _delivers_atmos,
    _TrackedDownload,
)

ATMOS = AudioMode.dolby_atmos.value
ATMOS_TIER = str(getattr(ATMOS_REQUEST_QUALITY, "value", ATMOS_REQUEST_QUALITY))


# --------------------------------------------------------------------------- #
# Fixtures: real store, real gate, real query.
# --------------------------------------------------------------------------- #
def _store(tmp_path) -> OwnershipStore:
    return OwnershipStore(str(tmp_path / "own.db"))


def _file(tmp_path, name):
    p = tmp_path / name
    p.write_text("audio")
    return str(p)


def _track(tid="101", *, atmos=True, modes=None):
    """A REAL Track, built without the network-touching __init__ the same way
    _gate builds the download.

    It has to be a real one: the engine's Atmos-only exclusion tests
    ``isinstance(media, Track)`` and so does the bridge mirror of it, and a
    SimpleNamespace slips straight past both. A fixture that dodges a production
    guard makes every test over it agree with a bug.

    ``modes`` spells the list out for the dual-mode case (a track TIDAL offers in
    both Atmos and stereo), which is a different question from either single-mode
    shape."""
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.artist = SimpleNamespace(name="Artist")
    t.audio_modes = list(modes) if modes is not None else ([ATMOS] if atmos else ["STEREO"])
    return t


def _gate(store, *, target, atmos_on):
    """The real ownership gate, on a _TrackedDownload built without the engine's
    network-touching __init__."""
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = store.ownership_of
    dl._target_rank = quality_rank(target)
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos_on))
    return dl


class _InlinePool:
    def start(self, worker):
        worker.run()


def _bridge(store, *, quality, atmos_on):
    """The real ownershipOf/_rollup_verdict, on a WavesBridge carcass."""
    b = WavesBridge.__new__(WavesBridge)
    b._ownership = store
    b._own_cache = {}
    b._own_lock = Lock()
    b._own_pending = set()
    b._own_pool = _InlinePool()
    b._announce_ownership = lambda tid: None
    b._downloads_running = lambda: False
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality, download_dolby_atmos=atmos_on))
    for name in (
        "ownershipOf",
        "_would_refetch_atmos",
        "_target_quality_rank",
        "_own_refresh",
        "_evict_own_cache_locked",
        "_rollup_verdict",
    ):
        setattr(b, name, getattr(WavesBridge, name).__get__(b, WavesBridge))
    return b


def _settled(bridge, tid):
    """ownershipOf with its background refresh landed: the first call schedules
    it and answers from the (empty) cache, the second reads the real answer."""
    bridge.ownershipOf(tid)
    return bridge.ownershipOf(tid)


# --------------------------------------------------------------------------- #
# The premise: an Atmos delivery cannot reach a Lossless target.
# --------------------------------------------------------------------------- #
def test_the_atmos_request_tier_ranks_below_every_lossless_target():
    """Read off the real constant, not the string it happens to equal today. If
    upstream ever raises ATMOS_REQUEST_QUALITY this stops being a defect, and
    this test is where that shows up."""
    atmos_rank = quality_rank(ATMOS_TIER)
    assert atmos_rank < quality_rank(Quality.high_lossless.value)
    assert atmos_rank < quality_rank(Quality.hi_res_lossless.value)


def _engine_took_the_atmos_session(media, atmos_on: bool) -> bool:
    """Drive the REAL Download._get_track_stream_info and report which session
    it reached for. Only the session and the stream are stand-ins; the branch
    under observation is the engine's own."""
    reached: list[str] = []
    stream = SimpleNamespace(get_stream_manifest=lambda: SimpleNamespace(file_extension=".m4a", codecs="EAC3"))
    dl = Download.__new__(Download)
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=atmos_on, extract_flac=False))
    dl.fn_logger = SimpleNamespace(error=lambda *a, **k: None, info=lambda *a, **k: None)
    dl.tidal = SimpleNamespace(
        switch_to_atmos_session=lambda: (reached.append("atmos"), True)[1],
        restore_normal_session=lambda: (reached.append("normal"), True)[1],
    )
    dl.session = SimpleNamespace(track=lambda _id: SimpleNamespace(get_stream=lambda: stream))
    media.get_stream = lambda: stream
    dl._get_track_stream_info(media)
    return reached == ["atmos"]


def test_waves_mirrors_the_engines_own_atmos_condition():
    """_delivers_atmos claims to be the engine's want_atmos, and the whole fix
    rests on that claim. Check all four combinations against the engine itself
    rather than against a transcription of it."""
    for atmos_on in (True, False):
        for has_atmos in (True, False):
            media = _track(atmos=has_atmos)
            engine = _engine_took_the_atmos_session(media, atmos_on)
            assert _delivers_atmos(media, atmos_on) is engine, (atmos_on, has_atmos, engine)


# --------------------------------------------------------------------------- #
# The gate: an Atmos copy is not re-fetched.
# --------------------------------------------------------------------------- #
def test_an_atmos_copy_is_not_re_fetched_at_a_lossless_target(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    dl = _gate(store, target="LOSSLESS", atmos_on=True)
    verdict, rec = dl._ownership_decision(_track())
    assert verdict == "skip", f"an Atmos copy cannot be upgraded to LOSSLESS, so forcing only rewrites it: {rec}"


def test_an_atmos_copy_is_not_re_fetched_at_a_max_target(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    dl = _gate(store, target="HI_RES_LOSSLESS", atmos_on=True)
    assert dl._ownership_decision(_track())[0] == "skip"


def test_the_atmos_gate_settles_instead_of_forcing_forever(tmp_path):
    """The shape of the defect was that a forced re-download recorded the same
    tier again, so the next download forced again. Walk two rounds, recording in
    between exactly what a re-fetch delivers."""
    store = _store(tmp_path)
    path = _file(tmp_path, "song.m4a")
    dl = _gate(store, target="HI_RES_LOSSLESS", atmos_on=True)
    seen = []
    for _ in range(2):
        store.record("101", path, ATMOS_TIER, audio_mode=ATMOS)
        seen.append(dl._ownership_decision(_track())[0])
    assert seen == ["skip", "skip"], f"the gate never settles: {seen}"


def test_turning_atmos_off_still_upgrades_an_atmos_copy_to_stereo(tmp_path):
    """The mirror image, and it must NOT change: with Atmos off the job would
    fetch a stereo LOSSLESS file, which the Atmos copy genuinely is not.

    Asked of a DUAL-MODE track, because that is the only shape the rationale
    describes. There has to BE a stereo stream for "would fetch a stereo
    LOSSLESS file" to mean anything, and an Atmos-only track has none (see the
    test below). This case used to be written against an Atmos-only track and
    passed for the wrong reason."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    dl = _gate(store, target="LOSSLESS", atmos_on=False)
    assert dl._ownership_decision(_track(modes=[ATMOS, "STEREO"]))[0] == "force"


def test_an_atmos_only_track_you_hold_is_not_forced_when_atmos_is_off(tmp_path):
    """An Atmos-only track has no stereo stream, so a re-fetch takes the Atmos
    session whatever the setting says (the engine's "nothing else to fetch"
    clause, which _delivers_atmos mirrors) and would deliver exactly the copy
    already on disk. The copy is therefore current and the gate skips: an
    album of owned Atmos-only tracks must never re-download itself, and it
    must never read as "nothing landed" over files that are all present."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    dl = _gate(store, target="LOSSLESS", atmos_on=False)
    verdict, rec = dl._ownership_decision(_track())
    assert verdict == "skip", f"an owned Atmos copy of an Atmos-only track is current: {rec}"


def test_an_all_atmos_album_you_hold_reports_finished_not_failed(tmp_path):
    """The user-visible end of it: every track is Atmos-only and owned, the
    gate skips each one, _emit_skip counts a skip as a handled outcome
    (ok_count += 1), and the collection has something to show for itself, so it
    finishes. An album whose every file is already on disk must never be a red
    row. (An all-Atmos album NOT on disk simply downloads now, so there is no
    second verdict to check: nothing is kept out by the setting anymore.)"""
    store = _store(tmp_path)
    tids = ("101", "102", "103")
    for tid in tids:
        store.record(tid, _file(tmp_path, f"song{tid}.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    dl = _gate(store, target="LOSSLESS", atmos_on=False)

    verdicts = [dl._ownership_decision(_track(tid))[0] for tid in tids]
    assert verdicts == ["skip", "skip", "skip"], verdicts
    assert (
        _collection_incomplete_reason(0, len(tids), 0) is None
    ), "an album whose every file is already on disk reported itself incomplete"


def test_an_atmos_only_track_you_do_not_hold_is_still_left_to_the_engine(tmp_path):
    """With nothing owned the gate says nothing, and the engine downloads the
    track through the Atmos session (there is nothing else to fetch). The skip
    above rides on there being a copy to keep, never on the track's modes."""
    store = _store(tmp_path)
    dl = _gate(store, target="LOSSLESS", atmos_on=False)
    assert dl._ownership_decision(_track())[0] is None


def test_an_owned_stereo_copy_is_left_alone_when_atmos_is_switched_on(tmp_path):
    """Deliberately NOT changed. A track can hold a stereo copy and an Atmos
    copy at once (different codecs, so different files and two rows), and
    ownership_of answers with the highest tier among them, which is the stereo
    one. Forcing on that mismatch would re-fetch the Atmos file already on disk,
    on every download: the same loop this file exists to close."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.flac"), "LOSSLESS", audio_mode="STEREO")
    dl = _gate(store, target="LOSSLESS", atmos_on=True)
    assert dl._ownership_decision(_track())[0] == "skip"


def test_a_stereo_copy_below_the_target_is_still_an_upgrade(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), "HIGH", audio_mode="STEREO")
    dl = _gate(store, target="LOSSLESS", atmos_on=True)
    assert dl._ownership_decision(_track(atmos=False))[0] == "force"


def test_a_stereo_copy_at_the_target_is_still_a_skip(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.flac"), "LOSSLESS", audio_mode="STEREO")
    dl = _gate(store, target="LOSSLESS", atmos_on=False)
    assert dl._ownership_decision(_track(atmos=False))[0] == "skip"


def test_a_tier_less_video_record_is_still_a_skip(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "clip.mp4"), None)
    dl = _gate(store, target="HI_RES_LOSSLESS", atmos_on=True)
    assert dl._ownership_decision(SimpleNamespace(id="101"))[0] == "skip"


def test_a_record_from_before_the_mode_column_reads_as_stereo(tmp_path):
    """An old row carries no audio_mode. It is treated as stereo, which costs
    one re-download and then settles, rather than being guessed at."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER)  # no audio_mode
    dl = _gate(store, target="LOSSLESS", atmos_on=True)
    assert dl._ownership_decision(_track())[0] == "force"


# --------------------------------------------------------------------------- #
# The query: the button and the album card tell the truth.
# --------------------------------------------------------------------------- #
def test_an_atmos_copy_reads_as_up_to_date_at_a_max_setting(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    b = _bridge(store, quality=Quality.hi_res_lossless, atmos_on=True)
    info = _settled(b, "101")
    assert info["owned"] is True
    assert info["up_to_date"] is True, "the button would sit on DOWNLOAD over a file that is already there"


def test_an_atmos_copy_reads_as_out_of_date_once_atmos_is_switched_off(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    b = _bridge(store, quality=Quality.hi_res_lossless, atmos_on=False)
    assert _settled(b, "101")["up_to_date"] is False


def test_an_atmos_album_card_reads_as_downloaded(tmp_path):
    """_rollup_verdict answers "no" for the whole card as soon as one member is
    not up_to_date, so one Atmos track used to un-say a finished album."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "01.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    store.record("102", _file(tmp_path, "02.flac"), "LOSSLESS", audio_mode="STEREO")
    b = _bridge(store, quality=Quality.high_lossless, atmos_on=True)
    for tid in ("101", "102"):
        _settled(b, tid)
    assert b._rollup_verdict(["101", "102"]) == "owned"


# --------------------------------------------------------------------------- #
# The prediction: the drawer promises what the run will actually do.
# --------------------------------------------------------------------------- #
def _predictor(store, *, quality, atmos_on):
    b = WavesBridge.__new__(WavesBridge)
    b._ownership = store
    b._redownload_overrides = set()
    b._library_claim_overrides = set()
    b._merge_plans = {}
    b._objs = {"album": {}}
    b._queue_index = {}
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality, download_dolby_atmos=atmos_on))
    b._library_claim_media = lambda media, album=None: False
    for name in ("_predict_skips", "_target_quality_rank", "_job_quality", "_job_library_skip", "_queue_item"):
        setattr(b, name, getattr(WavesBridge, name).__get__(b, WavesBridge))
    return b


def _row():
    # askLibrarySkip is pinned on the row by _enqueue; off here, so these tests
    # are about the ownership half of the gate alone.
    return {"qid": 1, "media_id": "alb-1", "type": "album", "collection": True, "askLibrarySkip": False}


def test_the_queue_predicts_a_skip_for_an_owned_atmos_track(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    monkeypatch.setattr(backend.WavesBridge, "_job_quality", lambda self, qid: Quality.hi_res_lossless)
    b = _predictor(store, quality=Quality.hi_res_lossless, atmos_on=True)
    marks = b._predict_skips(1, _row(), [_track()])
    # The drawer says ATMOS for an Atmos copy, never the tier it was requested
    # at: that tier is one fixed rung the quality setting cannot raise, so under
    # a HI-RES job the word HIGH read like a copy the run should have upgraded.
    assert marks == {"101": {"kind": "own", "tier": backend.ATMOS_WORD}}, marks


def test_an_atmos_copy_reads_atmos_on_every_drawer_surface(tmp_path, monkeypatch):
    """The gate's own skip mark, the drawer's prediction and a landed track's
    delivered event all pass through _delivered_word, so an Atmos copy reads
    ATMOS wherever it appears, and a stereo copy still reads its tier."""
    assert backend._delivered_word("HIGH", ATMOS) == backend.ATMOS_WORD
    assert backend._delivered_word("HIGH", "dolby_atmos") == backend.ATMOS_WORD
    assert backend._delivered_word("HIGH", "STEREO") == "HIGH"
    assert backend._delivered_word("HIGH", None) == "HIGH"
    assert backend._delivered_word("HI_RES_LOSSLESS", None) == "HI-RES"
    assert backend._delivered_word(None, None) == ""
    # ATMOS is a kind, not a rung: it sorts after the whole ladder in a MIXED
    # rollup rather than being dropped or filed among the tiers.
    reg = {"1": {"quality": "HI-RES"}, "2": {"quality": backend.ATMOS_WORD}, "3": {"quality": "LOSSLESS"}}
    landed, mix = backend._delivered_rollup(reg)
    assert landed == "" and [m["q"] for m in mix] == ["HI-RES", "LOSSLESS", backend.ATMOS_WORD]
    # The gate's skip mark carries the same word the prediction does.
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    monkeypatch.setattr(backend.WavesBridge, "_job_quality", lambda self, qid: Quality.hi_res_lossless)
    b = _predictor(store, quality=Quality.hi_res_lossless, atmos_on=True)
    marks = b._predict_skips(1, _row(), [_track()])
    dl = _gate(store, target=Quality.hi_res_lossless.value, atmos_on=True)
    dl._library_claim = None
    dl._force_redownload = False
    verdict, mark = dl._claim_decision(_track())
    assert verdict == "skip"
    assert mark["tier"] == marks["101"]["tier"] == backend.ATMOS_WORD


def test_the_prediction_and_the_gate_agree_about_an_atmos_track(tmp_path, monkeypatch):
    """_predict_skips exists to say in advance what the gate will decide, so the
    two must not answer differently. Both are driven here, on one store."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.m4a"), ATMOS_TIER, audio_mode=ATMOS)
    monkeypatch.setattr(backend.WavesBridge, "_job_quality", lambda self, qid: Quality.high_lossless)
    for atmos_on in (True, False):
        b = _predictor(store, quality=Quality.high_lossless, atmos_on=atmos_on)
        predicted = "101" in b._predict_skips(1, _row(), [_track()])
        gated = _gate(store, target="LOSSLESS", atmos_on=atmos_on)._ownership_decision(_track())[0] == "skip"
        assert predicted is gated, f"drawer says skip={predicted}, the run says skip={gated} (atmos on: {atmos_on})"
