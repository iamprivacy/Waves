"""An album, playlist or mix button judges its members by the COLLECTION's
own ask, the one its job pins for every member.

WHAT THIS FENCES OFF
--------------------
A collection's job reads the quality choice standing on the collection (its
own id), else the setting, and pins that ONE ask (tier and Atmos answer) on
every member (``_download``, ``_ask_quality_for``, ``_ask_atmos_for``). The
card's rollup used to ask ``ownershipOf`` per member, which answers for a
click on THAT TRACK: the track's own choice, else its album's (only while the
track object happened to be held), else the setting. The two disagreed
whenever the collection carried a choice its tracks did not inherit on the
card:

- Download Dolby Atmos off, ATMOS chosen on an album card (tracks not held),
  the album downloaded in Atmos: the card read DOWNLOAD and a click skipped
  every track (issue #40 class: a button offering a download that fetches
  nothing).
- The mirror: the setting on, LOSSLESS chosen on an album held in Atmos: the
  card read DOWNLOADED while the job would have force-fetched stereo.
- A track's own choice leaked into its album's header, which the job ignores.

HOW THIS STAYS FIXED
--------------------
The real ``ownershipOf`` and rollup (``_rollup_scan`` through
``_collection_ask``) on a WavesBridge carcass over a real OwnershipStore, with
the job's gate (``_TrackedDownload._ownership_decision``, pinned as the row
would pin it) run beside it on the same records: the two must agree in every
case, held track objects or not. The deliberate "an owned stereo copy stays
current under an ATMOS choice" rule (see _copy_is_current) is pinned on both
sides too.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from tidalapi.media import Quality, Track

from waves.ownership import OwnershipStore, quality_rank
from waves.waves_ui.backend import WavesBridge, _TrackedDownload

ATMOS = "DOLBY_ATMOS"
STEREO_CEILING = quality_rank("HI_RES_LOSSLESS")


class _InlinePool:
    def start(self, worker):
        worker.run()


def _track(tid, album_id, modes=("STEREO", ATMOS)):
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.artist = SimpleNamespace(name="Artist")
    t.album = SimpleNamespace(id=album_id)
    t.audio_modes = list(modes)
    return t


def _bridge(store, *, atmos_on, overrides, held=None, quality=Quality.high_lossless):
    b = WavesBridge.__new__(WavesBridge)
    b._ownership = store
    b._own_cache = {}
    b._own_lock = Lock()
    b._own_pending = set()
    b._own_pool = _InlinePool()
    b._announce_ownership = lambda tid: None
    b._downloads_running = lambda: False
    b._quality_overrides = dict(overrides)
    b._objs = {"track": dict(held or {}), "album": {}}
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=quality, download_dolby_atmos=atmos_on))
    for name in (
        "ownershipOf",
        "_would_refetch_atmos",
        "_target_quality_rank",
        "_own_refresh",
        "_own_refresh_many",
        "_own_claim_cold",
        "_evict_own_cache_locked",
        "_rollup_verdict",
        "_rollup_scan",
        "_rollup_detail",
        "collectionOwnership",
        "collectionOwnershipFor",
        "collectionOwnershipDetail",
        "_ask_atmos_for",
        "_ask_quality_for",
        "_quality_override_key",
        "_override_target_rank",
        "_queued_quality_value",
        "_target_tier",
        "_collection_ask",
    ):
        setattr(b, name, getattr(WavesBridge, name).__get__(b, WavesBridge))
    return b


def _gate(store, *, target, atmos_pin):
    """The job's gate as its row pins it: _download reads the collection's
    ask and the runner hands it on (pinned_quality, atmos_on)."""
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = store.ownership_of
    dl._target_rank = quality_rank(target)
    dl._atmos_on = atmos_pin
    # The setting disagrees with the pin on purpose: the pin must win.
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=not atmos_pin))
    return dl


def _verdict(b, cid):
    b.collectionOwnership(cid)  # cold: claims and lands the (inline) refresh
    return b.collectionOwnership(cid)["verdict"]


def _atmos_album(tmp_path, ids):
    """What an Atmos fetch records: LOW (TIDAL's filing tier for every Atmos
    stream), DOLBY_ATMOS, no requested rank, the advertised stereo ceiling."""
    store = OwnershipStore(str(tmp_path / "own.db"))
    for tid in ids:
        p = tmp_path / f"{tid}.m4a"
        p.write_text("audio")
        store.record(tid, str(p), "LOW", audio_mode=ATMOS, requested_rank=-1, ceiling_rank=STEREO_CEILING)
    store.record_members_replace("A", list(ids))
    return store


def _stereo_album(tmp_path, ids, tier="LOSSLESS"):
    store = OwnershipStore(str(tmp_path / "own.db"))
    for tid in ids:
        p = tmp_path / f"{tid}.flac"
        p.write_text("audio")
        store.record(tid, str(p), tier, ceiling_rank=STEREO_CEILING)
    store.record_members_replace("A", list(ids))
    return store


def _gate_verdicts(gate, ids):
    return [gate._ownership_decision(_track(t, "A"))[0] for t in ids]


def test_an_atmos_choice_on_the_album_reads_its_atmos_copies_as_owned_with_the_setting_off(tmp_path):
    ids = ["t1", "t2"]
    store = _atmos_album(tmp_path, ids)
    held = {t: _track(t, "A") for t in ids}
    # The card (nothing held) and the page header (tracks held) agree with
    # the job, whose row pins askAtmos True from the album's choice.
    assert _verdict(_bridge(store, atmos_on=False, overrides={"A": "ATMOS"}), "A") == "owned"
    assert _verdict(_bridge(store, atmos_on=False, overrides={"A": "ATMOS"}, held=held), "A") == "owned"
    assert _gate_verdicts(_gate(store, target="LOSSLESS", atmos_pin=True), ids) == ["skip", "skip"]
    # Without the choice the album's job fetches stereo, and the Atmos copies
    # are not that: DOWNLOAD, and the job force-fetches. Still in agreement.
    assert _verdict(_bridge(store, atmos_on=False, overrides={}), "A") == "no"
    assert _gate_verdicts(_gate(store, target="LOSSLESS", atmos_pin=False), ids) == ["force", "force"]


def test_a_stereo_choice_on_the_album_reads_its_atmos_copies_as_stale_with_the_setting_on(tmp_path):
    ids = ["t1", "t2"]
    store = _atmos_album(tmp_path, ids)
    held = {t: _track(t, "A") for t in ids}
    assert _verdict(_bridge(store, atmos_on=True, overrides={"A": "LOSSLESS"}), "A") == "no"
    assert _verdict(_bridge(store, atmos_on=True, overrides={"A": "LOSSLESS"}, held=held), "A") == "no"
    assert _gate_verdicts(_gate(store, target="LOSSLESS", atmos_pin=False), ids) == ["force", "force"]
    # And with no choice, the setting's Atmos fetch finds them current.
    assert _verdict(_bridge(store, atmos_on=True, overrides={}), "A") == "owned"
    assert _gate_verdicts(_gate(store, target="LOSSLESS", atmos_pin=True), ids) == ["skip", "skip"]


def test_a_tracks_own_choice_does_not_reach_its_albums_button(tmp_path):
    """HI-RES chosen on one track of an album held at LOSSLESS, setting at
    LOSSLESS: the track's own button offers the upgrade, the album's does
    not (its job, asking at the setting, skips every member)."""
    ids = ["t1", "t2"]
    store = _stereo_album(tmp_path, ids)
    b = _bridge(store, atmos_on=False, overrides={"t1": "HI-RES"})
    assert _verdict(b, "A") == "owned"
    assert b.ownershipOf("t1")["up_to_date"] is False, "the track's own button must still offer its upgrade"
    assert b.collectionOwnershipFor(ids, "A") == "owned"
    assert b.collectionOwnershipDetail(ids, "A")["verdict"] == "owned"
    assert _gate_verdicts(_gate(store, target="LOSSLESS", atmos_pin=False), ids) == ["skip", "skip"]
    # The same member list judged with no collection answers per track, as
    # the callers that hold only ids always did.
    assert b.collectionOwnershipFor(ids) == "no"


def test_an_atmos_choice_over_owned_stereo_copies_stays_downloaded_on_both_sides(tmp_path):
    """The deliberate rule (_copy_is_current): asking for Atmos does not make
    an owned stereo copy at or above the target read as stale, on the track,
    on the album, and in the job's gate alike. Redownload is the way to ask
    for the Atmos file."""
    ids = ["t1", "t2"]
    store = _stereo_album(tmp_path, ids)
    b = _bridge(store, atmos_on=False, overrides={"A": "ATMOS", "t1": "ATMOS"})
    assert _verdict(b, "A") == "owned"
    assert b.ownershipOf("t1")["up_to_date"] is True
    assert _gate_verdicts(_gate(store, target="LOSSLESS", atmos_pin=True), ids) == ["skip", "skip"]


def test_the_collection_ask_is_what_the_job_pins(tmp_path):
    store = _stereo_album(tmp_path, ["t1"])
    b = _bridge(store, atmos_on=False, overrides={"A": "ATMOS", "B": "HIGH", "C": "DEFAULT"})
    assert b._collection_ask("A") == (quality_rank("LOSSLESS"), True)
    assert b._collection_ask("B") == (quality_rank("HIGH"), False)
    assert b._collection_ask("C") == (quality_rank("LOSSLESS"), False)
    assert b._collection_ask("unchosen") == (quality_rank("LOSSLESS"), False)
    b.settings.data.download_dolby_atmos = True
    assert b._collection_ask("unchosen") == (quality_rank("LOSSLESS"), True)
    assert b._collection_ask("B") == (quality_rank("HIGH"), False), "a stereo tier chosen asks for stereo"
